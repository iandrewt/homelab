"""An image: its distribution's tree, baked by the baseline once per architecture, then built by mkosi
with the image's family and target over it, as a tree on its disk or as a signed image."""

import hashlib
import json
import os
import random
import subprocess
import time
import uuid
from pathlib import Path

import yaml

from ..bootstrap.shell import BootstrapError, run, say
from . import builder, images as catalog
from .images import Bake, Image
from .vm import SSH_OPTIONS, VSOCK_PROXY

# What a bake is a function of, so a change to any bakes again: as CI keys its cache, so a restored bake is
# current. Not the CIS role as installed, which records when: homelab.debian pins its version
BAKE_INPUTS = ("images/mkosi.conf", "images/mkosi.conf.d/debian", "images/mkosi.profiles/bake",
               "ansible/baseline.yml", "ansible/baseline", "ansible/collections", "ansible/requirements.yml")

# A login nothing in the bake's tree makes, which the seal removes with whatever else a credential made
BAKE_LOGIN = """\
d /home/debian 0700 debian debian -
d /home/debian/.ssh 0700 debian debian -
f /home/debian/.ssh/authorized_keys 0600 debian debian - {key}\\n
f /etc/sudoers.d/90-credentials 0440 root root - debian ALL=(ALL) NOPASSWD:ALL\\n
"""

# One root session: the login the seal removes is gone before the tar starts, and journald stops writing
SEAL_AND_TAR = ("sudo sh -c '/usr/lib/homelab/seal >&2"
                " && systemctl stop systemd-journald.socket systemd-journald-dev-log.socket"
                " systemd-journald-audit.socket systemd-journald.service"
                " && tar --create --numeric-owner --xattrs --acls --one-file-system --directory / .'")

# Until a release signs with KMS: a build's own, which its test VMs enroll
TEST_KEYS = """
set -eu
mkdir -p /out/keys/tree/usr/lib/systemd /out/keys/gnupg
chmod 700 /out/keys/gnupg
openssl req -quiet -new -x509 -newkey rsa:2048 -nodes -sha256 -days 3650 \\
  -subj "/CN=homelab image test/" -keyout /out/keys/db.key -out /out/keys/db.crt 2>/dev/null
export GNUPGHOME=/out/keys/gnupg
gpg --batch --quiet --passphrase '' --quick-gen-key 'homelab image test updates' ed25519 sign never
gpg --export > /out/keys/tree/usr/lib/systemd/import-pubring.pgp
"""

# What sysupdate reads: each version's UKI and /usr partitions, and their sums, signed
UPDATES = """
mkdir -p /out/updates
mv /cache/output/{name}_{version}.efi /cache/output/{name}_{version}.usr-*.raw /out/updates/
cd /out/updates
rm -f SHA256SUMS SHA256SUMS.gpg
sha256sum -- *.efi *.raw > SHA256SUMS
GNUPGHOME=/out/keys/gnupg gpg --batch --quiet --yes --detach-sign --output SHA256SUMS.gpg SHA256SUMS
"""


def register(verbs) -> None:
    build = verbs.add_parser(
        "build", help="build an image",
        description="Bakes the image's distribution for its architecture, unless the bake is current, then "
        f"builds the image over it with mkosi, into {builder.CACHE}/<image>/<image>.<format>, with its "
        "package manifest beside it.",
    )
    build.add_argument("image", help="an image in images/images.toml, e.g. debian-13-amd64-vm")
    build.add_argument("--version", default="dev", help="what its release file names it (default: dev)")
    build.set_defaults(run=_build)

    bake = verbs.add_parser(
        "bake", help="bake the distribution for an architecture",
        description="Builds the bake with mkosi, boots it, runs the baseline, and seals it into "
        f"{builder.OUTPUT}/bake_<architecture>.tar, which every image of the architecture lays over.",
    )
    bake.add_argument("architecture", help="as mkosi names it, e.g. x86-64")
    bake.set_defaults(run=_bake_verb)


def _build(args) -> int:
    images = builder.ensure()
    image = catalog.load(images, args.image)
    bake(images, image.architecture)
    out = builder.CACHE / image.name
    out.mkdir(parents=True, exist_ok=True)
    if image.image_based:
        _keys(out)
        lay_out(images, image, out, args.version)
        builder.run_in({out: "/out", builder.CACHE: "/cache"}, "rm", "-rf", "/out/updates")
        builder.run_in({out: "/out", builder.CACHE: "/cache"}, "sh", "-ec",
                       UPDATES.format(name=image.name, version=args.version))
    else:
        say(f"building {image.name} over its bake")
        _release(images.parent, image, out, args.version)
        _clear(f"{image.name}_{args.version}")
        builder.mkosi(images, *image.args, "--image-version", args.version, "--extra-tree", "/out/release:/",
                      "build", mounts={out: "/out"})
    _collect(image, out, args.version)
    say(f"built {out / f'{image.name}.{image.format}'}")
    return 0


def _bake_verb(args) -> int:
    bake(builder.ensure(), args.architecture, force=True)
    return 0


def bake(images: Path, architecture: str, force: bool = False) -> Path:
    """The baked tar, baked again unless what it was baked from is unchanged."""
    recipe = Bake(architecture)
    tar = builder.OUTPUT / recipe.tar
    stamp = tar.with_suffix(".inputs")
    inputs = _fingerprint(images.parent, *BAKE_INPUTS)
    if not force and tar.is_file() and stamp.is_file() and stamp.read_text() == inputs:
        say(f"the {architecture} bake is current")
        return tar
    stamp.unlink(missing_ok=True)
    say(f"building the {architecture} bake")
    _clear(f"bake_{architecture}")
    builder.mkosi(images, "--architecture", architecture, "--profile", "bake", "build")
    work = builder.CACHE / f"bake-{architecture}"
    if work.exists():
        builder.run_in({builder.CACHE: "/cache"}, "rm", "-rf", f"/cache/{work.name}")
    (work / "credentials").mkdir(parents=True)
    key = work / "ssh-key"
    run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "bake", "-f", str(key))
    (work / "credentials" / "sysusers.extra").write_text('u debian - "Debian" /home/debian /bin/bash\nm debian sudo\n')
    (work / "credentials" / "tmpfiles.extra").write_text(
        BAKE_LOGIN.format(key=key.with_suffix(".pub").read_text().strip()))
    (work / "vsock-proxy").write_text(VSOCK_PROXY)

    cid = random.randrange(1 << 16, 1 << 31)
    name = f"homelab-bake-{uuid.uuid4().hex[:12]}"
    ssh = ["ssh", "-i", str(key), *SSH_OPTIONS, "-o", f"ProxyCommand=python3 {work / 'vsock-proxy'} {cid}",
           "debian@bake"]
    say("booting it")
    builder.mkosi(images, "--architecture", architecture, "--profile", "bake", "--vsock-cid", str(cid),
                  "--credential", "/work/credentials", "vm", mounts={work: "/work"}, detach=name,
                  devices=tuple(d for d in ("/dev/kvm", "/dev/vhost-vsock") if os.path.exists(d)))
    try:
        _wait_for(ssh, name)

        say("baking in the baseline")
        _baseline(images.parent, work, ssh)

        say("sealing it")
        (work / "packages").write_text(run(*ssh, "dpkg-query --show --showformat '${Package} ${Version}\\n'",
                                           capture=True))
        partial = tar.with_suffix(".partial")
        with open(partial, "wb") as stream:
            sealed = subprocess.run([*ssh, SEAL_AND_TAR], stdout=stream, check=False)
        # tar exits 1 for a file that changed as it was read, which the seal's last writes can be
        if sealed.returncode not in (0, 1):
            raise BootstrapError(f"sealing the {architecture} bake failed: see {work / 'serial.log'}")
        partial.rename(tar)
    finally:
        logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True, check=False)
        (work / "serial.log").write_text(logs.stdout + logs.stderr)
        subprocess.run(["docker", "rm", "--force", name], capture_output=True, check=False)
    stamp.write_text(inputs)
    return tar


def _wait_for(ssh: list[str], container: str, timeout: int = 1800) -> None:
    deadline = time.monotonic() + timeout
    while subprocess.run([*ssh, "true"], capture_output=True, check=False).returncode:
        running = subprocess.run(["docker", "inspect", "--format", "{{.State.Running}}", container],
                                 capture_output=True, text=True, check=False).stdout.strip()
        if running != "true":
            raise BootstrapError(f"the bake's VM stopped: `docker logs {container}`")
        if time.monotonic() > deadline:
            raise BootstrapError(f"no SSH to the bake in {timeout}s: `docker logs {container}`")
        time.sleep(5)


def lay_out(images: Path, image: Image, out: Path, version: str) -> None:
    """The image-based image, at a version, over its bake."""
    say(f"building {image.name} over its bake, at {version}")
    _release(images.parent, image, out, version)
    keys = {flag: f"/out/keys/{name}" for flag, name in (
        ("--secure-boot-key", "db.key"), ("--secure-boot-certificate", "db.crt"),
        # One key, as a release's: the kernel checks verity's signatures against db
        ("--verity-key", "db.key"), ("--verity-certificate", "db.crt"),
        ("--sign-expected-pcr-key", "db.key"), ("--sign-expected-pcr-certificate", "db.crt"))}
    _clear(f"{image.name}_{version}")
    builder.mkosi(images, *image.args, "--image-version", version, "--extra-tree", "/out/keys/tree:/",
                  "--extra-tree", "/out/release:/", *(a for flag, path in keys.items() for a in (flag, path)),
                  "build", mounts={out: "/out"})


def _collect(image: Image, out: Path, version: str) -> None:
    """The image's artifacts under its own name, where a test, a derivation and a release look."""
    built = f"{image.name}_{version}"
    for suffix in (image.format, "vmlinuz", "initrd"):
        if (builder.OUTPUT / f"{built}.{suffix}").exists():
            builder.run_in({builder.CACHE: "/cache"}, "mv", f"/cache/output/{built}.{suffix}",
                           f"/cache/{image.name}/{image.name}.{suffix}")
    manifest = json.loads((builder.OUTPUT / f"{built}.manifest").read_text())
    (out / f"{image.name}.packages").write_text("".join(
        f"{p['name']} {p['version']}\n" for p in sorted(manifest["packages"], key=lambda p: p["name"])))
    _own(out)


def _keys(out: Path) -> None:
    if not (out / "keys" / "tree" / "usr" / "lib" / "systemd" / "import-pubring.pgp").exists():
        builder.run_in({out: "/out"}, "sh", "-c", "rm -rf /out/keys\n" + TEST_KEYS)


def _baseline(root: Path, work: Path, ssh: list[str]) -> None:
    """The baseline as the trees run it, with nothing of NetBox or 1Password loaded."""
    ansible = root / "ansible"
    inventory = work / "inventory.yml"
    inventory.write_text(yaml.safe_dump({"all": {"children": {"images": {"hosts": {"bake": {
        "ansible_user": "debian", "ansible_ssh_private_key_file": ssh[2],
        "ansible_ssh_common_args": " ".join(f"'{a}'" if " " in a else a for a in ssh[3:-1]),
    }}}}}}))
    env = os.environ | {
        "ANSIBLE_ROLES_PATH": str(ansible / "roles"),
        "ANSIBLE_COLLECTIONS_PATH": f"{ansible / 'collections'}:{Path.home() / '.ansible/collections'}",
        "ANSIBLE_PIPELINING": "true",
        "ANSIBLE_INTERPRETER_PYTHON": "auto_silent",
    }
    subprocess.run(["ansible-playbook", "-i", str(inventory), "-i", str(ansible / "baseline"),
                    str(ansible / "baseline.yml")], cwd=work, env=env, check=True)


def _release(root: Path, image: Image, out: Path, version: str) -> None:
    """What this image is, in os-release's format, for whatever re-asserts or derives from it."""
    git = ["git", "-C", str(root)]
    commit = run(*git, "rev-parse", "HEAD", capture=True).strip()
    dirty = run(*git, "status", "--porcelain", capture=True)
    collections = root / "ansible" / "collections" / "ansible_collections" / "homelab"
    release = out / "release" / "usr" / "lib" / "homelab" / "image-release"
    release.parent.mkdir(parents=True, exist_ok=True)
    release.write_text("".join(f"{key}={value}\n" for key, value in {
        "IMAGE_ID": image.name,
        "IMAGE_VERSION": version,
        "BUILD_ID": commit + ("-dirty" if dirty else ""),
        **{f"HOMELAB_{c.name.upper()}_VERSION": yaml.safe_load((c / "galaxy.yml").read_text())["version"]
           for c in sorted(collections.iterdir())},
    }.items()))


def _fingerprint(root: Path, *paths: str) -> str:
    """Every file's path and content under the paths, hashed, as they are in the working tree."""
    digest = hashlib.sha256()
    for path in map(root.joinpath, paths):
        for f in sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]:
            digest.update(str(f.relative_to(root)).encode() + b"\0" + f.read_bytes() + b"\0")
    return digest.hexdigest()


# mkosi's -f would remove its tools tree with them, which a build reuses
def _clear(output: str) -> None:
    builder.run_in({builder.OUTPUT: "/output"}, "find", "/output", "-maxdepth", "1", "-name", f"{output}*",
                   "-exec", "rm", "-rf", "{}", "+")


# The builder runs as root, so what it leaves is handed back
def _own(out: Path) -> None:
    if out.exists():
        builder.run_in({out: "/out"}, "find", "/out", "-maxdepth", "1", "-type", "f",
                       "-exec", "chown", f"{os.getuid()}:{os.getgid()}", "{}", "+")
