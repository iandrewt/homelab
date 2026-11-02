"""A profile's image: its tree, laid out, booted and baked by the baseline, then sealed."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import yaml

from ..bootstrap.shell import BootstrapError, run, say
from . import builder
from .vm import Vm

APT_SOURCES = "/etc/apt/sources.list.d/{release}.sources"

# Each archive at the recipe's snapshot, for the bake's installs; the seal's image takes the recipe's own
SNAPSHOT_SOURCES = """\
Types: deb
URIs: http://snapshot.debian.org/archive/debian/{snapshot}
Suites: {release} {release}-updates
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: http://snapshot.debian.org/archive/debian-security/{snapshot}
Suites: {release}-security
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
"""


def register(verbs) -> None:
    build = verbs.add_parser(
        "build", help="build a profile's image",
        description="Builds the tree with mkosi, lays it out with systemd-repart, boots it to bake in "
        f"the baseline, and seals it, into {builder.CACHE}/<profile>/<profile>.qcow2.",
    )
    build.add_argument("profile", help="a directory in images/, e.g. debian-13-amd64-vm")
    build.add_argument("--version", default="dev", help="what its release file names it (default: dev)")
    build.set_defaults(run=_build)


def _build(args) -> int:
    images = builder.ensure()
    recipe = images / args.profile
    if not (recipe / "mkosi.conf").is_file():
        raise BootstrapError(f"{args.profile!r} is not a profile in {images}")
    out = builder.CACHE / args.profile
    out.mkdir(parents=True, exist_ok=True)

    _tree_and_disk(images, recipe, out, args.version)
    _bake(images.parent, recipe, out)
    say(f"built {out / f'{args.profile}.qcow2'}")
    return 0


def _tree_and_disk(images: Path, recipe: Path, out: Path, version: str) -> None:
    mounts = {images: "/images:ro", out: "/out"}
    try:
        say("building the tree")
        builder.run_in(mounts, "mkosi", "--directory", f"/images/{recipe.name}",
                       "--output-dir", "/out", "--force", "build", privileged=True)
        builder.run_in(mounts, "sh", "-c", "mkdir -p /out/tree/usr/lib/homelab"
                       " && cat > /out/tree/usr/lib/homelab/image-release",
                       input=_release(images.parent, recipe.name, version))
        say("laying it out")
        builder.run_in(mounts, "rm", "-f", "/out/disk.raw")
        builder.run_in(mounts, "systemd-repart", "--empty=create", "--size=auto", "--offline=yes",
                       "--dry-run=no", "--definitions=/out/tree/usr/lib/repart.d",
                       "--copy-source=/out/tree", "/out/disk.raw")
        # What booting it takes, before the tree goes
        builder.run_in(mounts, "sh", "-c", "cp /out/tree/boot/vmlinuz-* /out/vmlinuz"
                       " && cp /out/tree/boot/initrd.img-* /out/initrd && rm -rf /out/tree /out/tree.vmlinuz")
    finally:
        _own(out)


def _bake(root: Path, recipe: Path, out: Path) -> None:
    work = out / "work"
    _clear(out, work)
    work.mkdir()
    for name in ("vmlinuz", "initrd"):
        shutil.copy(out / name, work / name)
    run("cp", "--sparse=always", str(out / "disk.raw"), str(work / "disk.raw"))

    config = (recipe / "mkosi.conf").read_text()
    release, snapshot = (re.search(rf"^{key}=(.+)$", config, re.MULTILINE).group(1) for key in ("Release", "Snapshot"))
    sources = APT_SOURCES.format(release=release)

    vm = Vm(work, "build")
    try:
        say("booting it")
        vm.start("disk.raw", kernel="vmlinuz", initrd="initrd",
                 append=(recipe / "mkosi.extra/etc/kernel/cmdline").read_text().strip())
        vm.wait_for_ssh()
        vm.ssh("cloud-init status --wait >/dev/null; sudo tee " + sources + " >/dev/null"
               " && echo 'Acquire::Check-Valid-Until \"false\";' | sudo tee /etc/apt/apt.conf.d/99snapshot >/dev/null",
               input=SNAPSHOT_SOURCES.format(release=release, snapshot=snapshot))

        say("baking in the baseline")
        _baseline(root, work, vm)

        say("installing the bootloader, and sealing")
        if (recipe / "install-bootloader").exists():
            vm.ssh("sudo sh -s", input=(recipe / "install-bootloader").read_text())
        vm.ssh(f"sudo tee {sources} >/dev/null && sudo rm /etc/apt/apt.conf.d/99snapshot",
               input=(recipe / "mkosi.extra" / sources.lstrip("/")).read_text())
        vm.seal()
    finally:
        vm.remove()

    say("converting it")
    builder.run_in({out: "/out"}, "qemu-img", "convert", "-O", "qcow2", "-c",
                   "/out/work/disk.raw", f"/out/{recipe.name}.qcow2")
    _own(out)
    _clear(out, work)


def _baseline(root: Path, work: Path, vm: Vm) -> None:
    """The baseline as the trees run it, with nothing of NetBox or 1Password loaded."""
    ansible = root / "ansible"
    env = os.environ | {
        "ANSIBLE_ROLES_PATH": str(ansible / "roles"),
        "ANSIBLE_COLLECTIONS_PATH": f"{ansible / 'collections'}:{Path.home() / '.ansible/collections'}",
        "ANSIBLE_PIPELINING": "true",
        "ANSIBLE_INTERPRETER_PYTHON": "auto_silent",
    }
    subprocess.run(["ansible-playbook", "-i", str(vm.inventory()), "-i", str(ansible / "baseline"),
                    str(ansible / "baseline.yml")], cwd=work, env=env, check=True)


def _release(root: Path, profile: str, version: str) -> str:
    """What this image is, in os-release's format, for whatever re-asserts or derives from it."""
    git = ["git", "-C", str(root)]
    commit = run(*git, "rev-parse", "HEAD", capture=True).strip()
    dirty = run(*git, "status", "--porcelain", capture=True)
    collections = root / "ansible" / "collections" / "ansible_collections" / "homelab"
    return "".join(f"{key}={value}\n" for key, value in {
        "IMAGE_ID": profile,
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
