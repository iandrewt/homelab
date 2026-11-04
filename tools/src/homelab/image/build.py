"""A profile's image: its tree, laid out, booted and baked by the baseline, then sealed; an image-based one
is then laid out as its image, over the baked tree."""

import os
import shutil
import subprocess
from pathlib import Path

import yaml

from ..bootstrap.shell import run, say
from . import builder, profile
from .profile import Profile
from .vm import Vm

REPART = "mkosi.extra/usr/lib/repart.d"
# The baked tree, booted as one root filesystem
BAKE_DISK = "8G"

# Until a release signs with KMS: a build's own, which its test VMs enroll
TEST_KEYS = """
set -eu
mkdir -p /out/keys/tree/usr/lib/systemd /out/keys/gnupg
chmod 700 /out/keys/gnupg
openssl req -quiet -new -x509 -newkey rsa:2048 -nodes -sha256 -days 3650 \
  -subj "/CN=homelab image test/" -keyout /out/keys/db.key -out /out/keys/db.crt 2>/dev/null
export GNUPGHOME=/out/keys/gnupg
gpg --batch --quiet --passphrase '' --quick-gen-key 'homelab image test updates' ed25519 sign never
gpg --export > /out/keys/tree/usr/lib/systemd/import-pubring.gpg
"""

# The VM's container can still hold the disk's loop device for a moment after it's gone
EXTRACT = """
rm -rf /out/baked
mkdir -p /mnt/baked /out/baked
for attempt in 1 2 3 4 5; do mount -o loop,ro /out/work/disk.raw /mnt/baked && break; sleep 3; done
cp -a /mnt/baked/. /out/baked/
umount /mnt/baked
"""

# What sysupdate reads: each version's UKI and /usr partitions, and their sums, signed
UPDATES = """
mkdir -p /out/updates
cd /out/stage2
mv *.efi *.usr-*.*.raw /out/updates/
cd /out/updates
rm -f SHA256SUMS SHA256SUMS.gpg
sha256sum -- *.efi *.raw > SHA256SUMS
GNUPGHOME=/out/keys/gnupg gpg --batch --quiet --yes --detach-sign --output SHA256SUMS.gpg SHA256SUMS
"""


def register(verbs) -> None:
    build = verbs.add_parser(
        "build", help="build a profile's image",
        description="Builds the tree with mkosi, lays it out, boots it to bake in the baseline, and seals "
        f"it, into {builder.CACHE}/<profile>/<profile>.<format>, with its package manifest beside it.",
    )
    build.add_argument("profile", help="a directory in images/, e.g. debian-13-amd64-vm")
    build.add_argument("--version", default="dev", help="what its release file names it (default: dev)")
    build.set_defaults(run=_build)


def _build(args) -> int:
    images = builder.ensure()
    image = profile.load(images, args.profile)
    out = builder.CACHE / image.name
    out.mkdir(parents=True, exist_ok=True)

    _tree_and_disk(images, image, out, args.version)
    _bake(images.parent, image, out)
    if image.bake:
        _keys(out)
        lay_out(images, image, out, args.version)
        builder.run_in({out: "/out"}, "rm", "-rf", "/out/updates")
        builder.run_in({out: "/out"}, "sh", "-ec", UPDATES)
        disk = f"/out/stage2/{image.name}_{args.version}.raw"
        builder.run_in({out: "/out"}, "sh", "-c", f"qemu-img convert -O qcow2 -c {disk} /out/{image.name}.qcow2"
                       " && rm -rf /out/stage2")
        _own(out)
    say(f"built {out / f'{image.name}.{image.format}'}")
    return 0


def _tree_and_disk(images: Path, image: Profile, out: Path, version: str) -> None:
    mounts = {images: "/images:ro", out: "/out"}
    kernel, initrd = image.kernel
    try:
        say("building the tree")
        builder.run_in(mounts, "mkosi", "--directory", f"/images/{image.tree.relative_to(images)}",
                       "--output-dir", "/out", "--force", "build", privileged=True)
        builder.run_in(mounts, "sh", "-c", "mkdir -p /out/tree/usr/lib/homelab"
                       " && cat > /out/tree/usr/lib/homelab/image-release",
                       input=_release(images.parent, image.name, version))
        say("laying it out")
        builder.run_in(mounts, "rm", "-f", "/out/disk.raw")
        # repart where the recipe declares partitions it can make, else the recipe's own layout
        if image.bake:
            builder.run_in(mounts, "mkfs.ext4", "-q", "-L", "root", "-d", "/out/tree", "/out/disk.raw", BAKE_DISK)
        elif (image.recipe / REPART).is_dir():
            builder.run_in(mounts, "systemd-repart", "--empty=create", "--size=auto", "--offline=yes",
                           "--dry-run=no", "--definitions=/out/tree/usr/lib/repart.d",
                           "--copy-source=/out/tree", "/out/disk.raw")
        else:
            builder.run_in(mounts, f"/images/{image.name}/layout", "/out/tree", "/out/disk.raw", privileged=True)
        # What booting it takes, before the tree goes
        builder.run_in(mounts, "sh", "-c", f"cp /out/tree/{kernel} /out/vmlinuz"
                       f" && cp /out/tree/{initrd} /out/initrd && rm -rf /out/tree /out/tree.vmlinuz")
    finally:
        _own(out)


def _bake(root: Path, image: Profile, out: Path) -> None:
    work = out / "work"
    _clear(out, work)
    work.mkdir()
    for name in ("vmlinuz", "initrd"):
        shutil.copy(out / name, work / name)
    run("cp", "--sparse=always", str(out / "disk.raw"), str(work / "disk.raw"))

    vm = Vm(work, "build", image)
    try:
        say("booting it")
        vm.start("disk.raw", kernel="vmlinuz", initrd="initrd")
        vm.wait_for_ssh()
        if image.cloud_init:
            vm.ssh("cloud-init status --wait >/dev/null || true")
        vm.ssh(f"sudo tee {image.sources} >/dev/null"
               " && echo 'Acquire::Check-Valid-Until \"false\";' | sudo tee /etc/apt/apt.conf.d/99snapshot >/dev/null",
               input=image.snapshot_sources)

        say("baking in the baseline")
        _baseline(root, vm)

        say("installing the bootloader, and sealing")
        if (image.recipe / "install-bootloader").exists():
            vm.ssh("sudo sh -s", input=(image.recipe / "install-bootloader").read_text())
        vm.ssh(f"sudo tee {image.sources} >/dev/null && sudo rm /etc/apt/apt.conf.d/99snapshot",
               input=(image.tree / "mkosi.extra" / image.sources.lstrip("/")).read_text())
        (out / f"{image.name}.packages").write_text(vm.ssh("dpkg-query --show --showformat '${Package} ${Version}\\n'"))
        vm.seal()
    finally:
        vm.remove()

    if image.bake:
        say("taking the baked tree out")
        builder.run_in({out: "/out"}, "sh", "-ec", EXTRACT, privileged=True)
        builder.run_in({out: "/out"}, "rm", "-f", "/out/disk.raw", "/out/vmlinuz", "/out/initrd")
    elif image.format == "qcow2":
        say("converting it")
        builder.run_in({out: "/out"}, "qemu-img", "convert", "-O", "qcow2", "-c",
                       "/out/work/disk.raw", f"/out/{image.name}.qcow2")
    else:
        builder.run_in({out: "/out"}, "mv", "/out/work/disk.raw", f"/out/{image.name}.raw")
    _own(out)
    _clear(out, work)


def lay_out(images: Path, image: Profile, out: Path, version: str) -> None:
    """The image, at a version, laid out over the baked tree, into out/stage2."""
    say(f"laying it out, at {version}")
    builder.run_in({out: "/out"}, "rm", "-rf", "/out/stage2")
    keys = {flag: f"/out/keys/{name}" for flag, name in (
        ("--secure-boot-key", "db.key"), ("--secure-boot-certificate", "db.crt"),
        # One key, as a release's: the kernel checks verity's signatures against db
        ("--verity-key", "db.key"), ("--verity-certificate", "db.crt"),
        ("--sign-expected-pcr-key", "db.key"), ("--sign-expected-pcr-certificate", "db.crt"))}
    builder.run_in({images: "/images:ro", out: "/out"}, "mkosi", "--directory", f"/images/{image.name}",
                   "--base-tree", "/out/baked", "--extra-tree", "/out/keys/tree:/", "--image-version", version,
                   *(a for flag, path in keys.items() for a in (flag, path)),
                   "--output-dir", "/out/stage2", "--force", "build", privileged=True)


def _keys(out: Path) -> None:
    if not (out / "keys" / "db.crt").exists():
        builder.run_in({out: "/out"}, "sh", "-c", TEST_KEYS)


def _baseline(root: Path, vm: Vm) -> None:
    """The baseline as the trees run it, with nothing of NetBox or 1Password loaded."""
    ansible = root / "ansible"
    env = os.environ | {
        "ANSIBLE_ROLES_PATH": str(ansible / "roles"),
        "ANSIBLE_COLLECTIONS_PATH": f"{ansible / 'collections'}:{Path.home() / '.ansible/collections'}",
        "ANSIBLE_PIPELINING": "true",
        "ANSIBLE_INTERPRETER_PYTHON": "auto_silent",
    }
    subprocess.run(["ansible-playbook", "-i", str(vm.inventory()), "-i", str(ansible / "baseline"),
                    str(ansible / "baseline.yml")], cwd=vm.work, env=env, check=True)


def _release(root: Path, name: str, version: str) -> str:
    """What this image is, in os-release's format, for whatever re-asserts or derives from it."""
    git = ["git", "-C", str(root)]
    commit = run(*git, "rev-parse", "HEAD", capture=True).strip()
    dirty = run(*git, "status", "--porcelain", capture=True)
    collections = root / "ansible" / "collections" / "ansible_collections" / "homelab"
    return "".join(f"{key}={value}\n" for key, value in {
        "IMAGE_ID": name,
        "IMAGE_VERSION": version,
        "BUILD_ID": commit + ("-dirty" if dirty else ""),
        **{f"HOMELAB_{c.name.upper()}_VERSION": yaml.safe_load((c / "galaxy.yml").read_text())["version"]
           for c in sorted(collections.iterdir())},
    }.items())


# The builder runs as root, so what it leaves is handed back, or removed by it
def _own(out: Path) -> None:
    builder.run_in({out: "/out"}, "find", "/out", "-maxdepth", "1", "-type", "f",
                   "-exec", "chown", f"{os.getuid()}:{os.getgid()}", "{}", "+")


def _clear(out: Path, work: Path) -> None:
    if work.exists():
        builder.run_in({out: "/out"}, "rm", "-rf", f"/out/{work.name}")
