"""What sysupdate reads: an image's versions in a channel of the update bucket, and their sums, signed by the
release key. A release publishes its build to canary, and promoting copies a version from canary to stable;
each channel keeps its newest versions, and its sums are checked against the release key before anything is
added to them."""

import os
import re
import subprocess
import tempfile
from pathlib import Path

from ..bootstrap.shell import BootstrapError, run, say
from . import build, builder, images as catalog, signing
from .images import Image

KEEP = 5

# The release key's keyring is the build's, which also verifies what the channel has
SIGN = """
set -eu
export GNUPGHOME=/out/keys/gnupg
cd /work
if [ -f old ]; then gpg --batch --quiet --verify old.gpg old; fi
gpg --batch --quiet --yes --detach-sign --output SHA256SUMS.gpg SHA256SUMS
"""


def register(verbs) -> None:
    publish = verbs.add_parser(
        "publish", help="publish an image-based release to its canary channel",
        description="Adds the release built in the cache to the image's canary channel of the update bucket, "
        f"keeping its newest {KEEP} versions, its sums signed with the release key. The bucket is IMAGES_BUCKET; "
        "AWS's credentials are the release role's.",
    )
    publish.add_argument("image", help="an image-based image in images/images.toml, e.g. ymir-amd64-vm")
    publish.set_defaults(run=_publish)

    promote = verbs.add_parser(
        "promote", help="promote a version of an image-based image from canary to stable",
        description=f"Copies a version from the image's canary channel to stable, keeping its newest {KEEP} "
        "versions, its sums signed with the release key.",
    )
    promote.add_argument("image", help="an image-based image in images/images.toml, e.g. ymir-amd64-vm")
    promote.add_argument("version", help="a version canary has, e.g. 2026.11.2")
    promote.set_defaults(run=_promote)


def merge(old: str, new: str, image: str, keep: int = KEEP) -> tuple[str, list[str]]:
    """A channel's sums with new's added, its newest `keep` versions kept, and the files it no longer holds."""
    lines = {}
    for line in (old + new).splitlines():
        if line.strip():
            lines[line.split(maxsplit=1)[1].lstrip("*")] = line
    versions: dict[str, list[str]] = {}
    for name in lines:
        versions.setdefault(version_of(name, image), []).append(name)
    newest = sorted(versions, key=_order, reverse=True)
    sums = "".join(f"{lines[name]}\n" for version in sorted(newest[:keep], key=_order) for name in sorted(versions[version]))
    return sums, sorted(name for version in newest[keep:] for name in versions[version])


def version_of(name: str, image: str) -> str:
    if not (match := re.match(rf"{re.escape(image)}_(.+?)\.(efi$|usr-)", name)):
        raise BootstrapError(f"{name} isn't a file of {image}'s")
    return match[1]


def _order(version: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d+(\.\d+)*", version):
        raise BootstrapError(f"a channel holds releases, versioned YYYY.MM.N, not {version}")
    return tuple(int(part) for part in version.split("."))


def _publish(args) -> int:
    images = builder.ensure()
    image = _image_based(images, args.image)
    out = builder.CACHE / image.name
    sums = (out / "updates" / "SHA256SUMS").read_text()
    work = signing.prepare(images, builder.CACHE / "signing")
    _update(image, out, work, "canary", sums, {name: str(out / "updates" / name) for name in _names(sums)})
    return 0


def _promote(args) -> int:
    images = builder.ensure()
    image = _image_based(images, args.image)
    out = builder.CACHE / image.name
    canary = f"s3://{_bucket()}/{image.name}/canary"
    sums = "".join(f"{line}\n" for line in _read(f"{canary}/SHA256SUMS").splitlines()
                   if line.strip() and version_of(line.split(maxsplit=1)[1], image.name) == args.version)
    if not sums:
        raise BootstrapError(f"canary has no {image.name} {args.version}")
    # A promotion builds nothing, so it makes the release key's keyring itself
    work = build.signer(images, image, "kms", out)
    _update(image, out, work, "stable", sums, {name: f"{canary}/{name}" for name in _names(sums)})
    return 0


def _update(image: Image, out: Path, work: Path, channel: str, sums: str, sources: dict[str, str]) -> None:
    """Adds sums' files to a channel, from sources local or in the bucket, and prunes what it no longer holds.
    The files go first and the sums last, so a client never reads sums whose files aren't there."""
    prefix = f"s3://{_bucket()}/{image.name}/{channel}"
    with tempfile.TemporaryDirectory(dir=builder.CACHE) as tmp:
        work_dir = Path(tmp)
        old = _read(f"{prefix}/SHA256SUMS")
        if old:
            if not (signature := _read(f"{prefix}/SHA256SUMS.gpg", binary=True)):
                raise BootstrapError(f"{prefix}/SHA256SUMS.gpg is missing, so the channel's sums can't be checked")
            (work_dir / "old").write_text(old)
            (work_dir / "old.gpg").write_bytes(signature)
        merged, pruned = merge(old, sums, image.name)
        (work_dir / "SHA256SUMS").write_text(merged)
        builder.run_in({work_dir: "/work", out: "/out", **signing.mounts(work)}, "sh", "-c", SIGN, env=signing.env())
        say(f"publishing {', '.join(sorted({version_of(n, image.name) for n in sources}))} to {prefix}")
        for name, source in sources.items():
            run("aws", "s3", "cp", "--only-show-errors", source, f"{prefix}/{name}")
        for name in ("SHA256SUMS.gpg", "SHA256SUMS"):
            run("aws", "s3", "cp", "--only-show-errors", str(work_dir / name), f"{prefix}/{name}")
        for name in pruned:
            run("aws", "s3", "rm", "--only-show-errors", f"{prefix}/{name}")
        if pruned:
            say(f"pruned {', '.join(sorted({version_of(n, image.name) for n in pruned}))} from {prefix}")


def _read(uri: str, binary: bool = False) -> str | bytes:
    """An object's content, or empty if there's none: the release role may list, so a missing one is a 404."""
    result = subprocess.run(["aws", "s3", "cp", "--only-show-errors", uri, "-"], capture_output=True, check=False)
    if result.returncode == 0:
        return result.stdout if binary else result.stdout.decode()
    if b"404" in result.stderr or b"Not Found" in result.stderr:
        return b"" if binary else ""
    raise BootstrapError(f"reading {uri} failed: {result.stderr.decode().strip()}")


def _names(sums: str) -> list[str]:
    return [line.split(maxsplit=1)[1].lstrip("*") for line in sums.splitlines() if line.strip()]


def _image_based(images: Path, name: str) -> Image:
    image = catalog.load(images, name)
    if not image.image_based:
        raise BootstrapError(f"{name} isn't image-based: nothing updates it in place")
    return image


def _bucket() -> str:
    if not (bucket := os.environ.get("IMAGES_BUCKET")):
        raise BootstrapError("IMAGES_BUCKET names the update bucket: platform/aws/images's output `bucket`")
    return bucket
