"""Platform's base images: each profile in images/ built in the one pinned container."""

import os
import tempfile
from pathlib import Path

from . import secrets
from .bootstrap.shell import BootstrapError, require, run, say
from .repo import Repo

CACHE = Path.home() / ".cache" / "homelab" / "images"
BUILDER = "homelab-image-builder"

KEY_VAULT = "Break-glass"
KEY_ITEM = "Secure Boot Key - "

# PK signs KEK and KEK signs db, which holds Debian's CA alone
SIGN_KEYS = """
set -eu
for k in PK KEK; do
  openssl req -quiet -new -x509 -newkey rsa:2048 -nodes -days 7300 \
    -subj "/CN=homelab Secure Boot $k (interim)/" -keyout $k.key -out $k.crt
done
openssl x509 -inform DER -in /secure-boot/debian-secure-boot-ca.der -out db.crt
owner=$(uuidgen)
mkdir auto
for var in PK:PK KEK:PK db:KEK; do
  name=${var%:*} signer=${var#*:}
  cert-to-efi-sig-list -g "$owner" $name.crt $name.esl
  sign-efi-sig-list -k $signer.key -c $signer.crt $name $name.esl auto/$name.auth >/dev/null
done
"""


def register(subparsers) -> None:
    image = subparsers.add_parser("image", help="build Platform's base images", description=__doc__)
    verbs = image.add_subparsers(dest="verb", required=True)

    build = verbs.add_parser(
        "build", help="build a profile's disk",
        description=f"Builds the tree with mkosi and lays it out with systemd-repart, into {CACHE}/<profile>.",
    )
    build.add_argument("profile", help="a directory in images/, e.g. debian-13-amd64-vm")
    build.set_defaults(run=_build)

    keys = verbs.add_parser(
        "keys", help="make the interim Secure Boot keys",
        description=f"Makes a PK and KEK, keeps their private keys in {KEY_VAULT}, and writes what every "
        "image enrolls to images/secure-boot/auto/. Refuses where keys exist: re-keying is D2's.",
    )
    keys.set_defaults(run=_keys)


def _build(args) -> int:
    images = _builder()
    if not (images / args.profile / "mkosi.conf").is_file():
        raise BootstrapError(f"{args.profile!r} is not a profile in {images}")
    out = CACHE / args.profile
    out.mkdir(parents=True, exist_ok=True)
    mounts = {images: "/images:ro", out: "/out"}

    try:
        say("building the tree")
        _in_builder(mounts, "mkosi", "--directory", f"/images/{args.profile}",
                    "--output-dir", "/out", "--force", "build", privileged=True)
        say("laying it out")
        _in_builder(mounts, "rm", "-f", "/out/disk.raw")
        _in_builder(mounts, "systemd-repart", "--empty=create", "--size=auto", "--offline=yes",
                    "--dry-run=no", "--definitions=/out/tree/usr/lib/repart.d",
                    "--copy-source=/out/tree", "/out/disk.raw")
        # What booting it takes, before the tree goes
        _in_builder(mounts, "sh", "-c", "cp /out/tree/boot/vmlinuz-* /out/vmlinuz"
                    " && cp /out/tree/boot/initrd.img-* /out/initrd && rm -rf /out/tree /out/tree.vmlinuz")
    finally:
        _in_builder(mounts, "find", "/out", "-maxdepth", "1", "-type", "f",
                    "-exec", "chown", f"{os.getuid()}:{os.getgid()}", "{}", "+")

    say(f"built {out / 'disk.raw'}")
    return 0


def _keys(args) -> int:
    images = _builder()
    published = images / "secure-boot"
    if (published / "auto").exists() or secrets.read_items(KEY_VAULT, KEY_ITEM):
        raise BootstrapError(f"Secure Boot keys exist (images/secure-boot/auto, {KEY_VAULT}): re-keying is D2's")

    # The runtime directory is a tmpfs, so the private keys never reach a disk
    with tempfile.TemporaryDirectory(dir=os.environ.get("XDG_RUNTIME_DIR")) as work:
        say("making PK and KEK, and signing what every image enrolls")
        _in_builder({published: "/secure-boot:ro", Path(work): "/work"}, "sh", "-c", SIGN_KEYS,
                    user=f"{os.getuid()}:{os.getgid()}", workdir="/work")
        for key in ("PK", "KEK"):
            fields = {(key, "private key"): (Path(work) / f"{key}.key").read_text(),
                      (key, "certificate"): (Path(work) / f"{key}.crt").read_text()}
            print(f"{secrets.write_item(KEY_VAULT, KEY_ITEM + key, fields, concealed={'private key'}, tags=['homelab/secure-boot'])}: {key}")
        (published / "auto").mkdir()
        for key in ("PK", "KEK"):
            (published / f"{key}.crt").write_bytes((Path(work) / f"{key}.crt").read_bytes())
        for var in ("PK", "KEK", "db"):
            (published / "auto" / f"{var}.auth").write_bytes((Path(work) / "auto" / f"{var}.auth").read_bytes())

    say(f"wrote {published.relative_to(images.parent)}")
    return 0


def _builder() -> Path:
    require("docker")
    images = Repo.discover().root / "images"
    say("building the builder")
    run("docker", "build", "--quiet", "--tag", BUILDER, "--file", str(images / "Containerfile"),
        str(images), capture=True)
    return images


# mkosi mounts its sandbox's API filesystems, which takes a privileged container
def _in_builder(mounts: dict[Path, str], *command: str, privileged: bool = False,
                user: str | None = None, workdir: str | None = None) -> None:
    run("docker", "run", "--rm", *(["--privileged"] if privileged else []),
        *(["--user", user] if user else []), *(["--workdir", workdir] if workdir else []),
        *(arg for host, guest in mounts.items() for arg in ("--volume", f"{host}:{guest}")),
        BUILDER, *command)
