"""Platform's base images: each profile in images/ built in the one pinned container."""

import os
from pathlib import Path

from .bootstrap.shell import BootstrapError, require, run, say
from .repo import Repo

CACHE = Path.home() / ".cache" / "homelab" / "images"
BUILDER = "homelab-image-builder"


def register(subparsers) -> None:
    image = subparsers.add_parser("image", help="build Platform's base images", description=__doc__)
    verbs = image.add_subparsers(dest="verb", required=True)

    build = verbs.add_parser(
        "build", help="build a profile's disk",
        description=f"Builds the tree with mkosi and lays it out with systemd-repart, into {CACHE}/<profile>.",
    )
    build.add_argument("profile", help="a directory in images/, e.g. debian-13-amd64-vm")
    build.set_defaults(run=_build)


def _build(args) -> int:
    require("docker")
    images = Repo.discover().root / "images"
    if not (images / args.profile / "mkosi.conf").is_file():
        raise BootstrapError(f"{args.profile!r} is not a profile in {images}")
    out = CACHE / args.profile
    out.mkdir(parents=True, exist_ok=True)

    say("building the builder")
    run("docker", "build", "--quiet", "--tag", BUILDER, "--file", str(images / "Containerfile"),
        str(images), capture=True)
    try:
        say("building the tree")
        _in_builder(images, out, "mkosi", "--directory", f"/images/{args.profile}",
                    "--output-dir", "/out", "--force", "build", privileged=True)
        say("laying it out")
        _in_builder(images, out, "rm", "-f", "/out/disk.raw")
        _in_builder(images, out, "systemd-repart", "--empty=create", "--size=auto", "--offline=yes",
                    "--dry-run=no", "--definitions=/out/tree/usr/lib/repart.d",
                    "--copy-source=/out/tree", "/out/disk.raw")
        # What booting it takes, before the tree goes
        _in_builder(images, out, "sh", "-c", "cp /out/tree/boot/vmlinuz-* /out/vmlinuz"
                    " && cp /out/tree/boot/initrd.img-* /out/initrd && rm -rf /out/tree /out/tree.vmlinuz")
    finally:
        _in_builder(images, out, "find", "/out", "-maxdepth", "1", "-type", "f",
                    "-exec", "chown", f"{os.getuid()}:{os.getgid()}", "{}", "+")

    say(f"built {out / 'disk.raw'}")
    return 0


# mkosi mounts its sandbox's API filesystems, which takes a privileged container
def _in_builder(images: Path, out: Path, *command: str, privileged: bool = False) -> None:
    run("docker", "run", "--rm", *(["--privileged"] if privileged else []),
        "--volume", f"{images}:/images:ro", "--volume", f"{out}:/out", BUILDER, *command)
