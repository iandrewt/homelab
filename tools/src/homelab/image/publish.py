"""What sysupdate reads, and a cell imports new machines from: an image's versions in a channel of the update
bucket, and their sums, signed by the release key. A release publishes its build to canary, and promoting copies
a version from canary to stable; each channel keeps its newest versions, and its sums are checked against the
release key before anything is added to them."""

import hashlib
import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Channel:
    """What a release adds its files to, under a prefix of the bucket, and how their names hold its version."""
    prefix: str
    # A file's name, its version the first group
    name: str
    # What a release's version looks like
    version: str

    def version_of(self, name: str) -> str:
        if not (match := re.fullmatch(self.name, name)):
            raise BootstrapError(f"{name} isn't a file of {self.prefix}'s")
        if not re.fullmatch(self.version, match[1]):
            raise BootstrapError(f"{self.prefix} holds releases, versioned as {self.version}, not {match[1]}")
        return match[1]


def image_channel(image: str) -> Channel:
    return Channel(image, rf"{re.escape(image)}_(.+?)\.(?:efi|usr-.*|qcow2)", r"\d+(\.\d+)*")


def merge(old: str, new: str, channel: Channel, keep: int = KEEP) -> tuple[str, list[str]]:
    """A channel's sums with new's added, its newest `keep` versions kept, and the files it no longer holds."""
    lines = {}
    for line in (old + new).splitlines():
        if line.strip():
            lines[line.split(maxsplit=1)[1].lstrip("*")] = line
    versions: dict[str, list[str]] = {}
    for name in lines:
        versions.setdefault(channel.version_of(name), []).append(name)
    newest = sorted(versions, key=_order, reverse=True)
    sums = "".join(f"{lines[name]}\n" for version in sorted(newest[:keep], key=_order) for name in sorted(versions[version]))
    return sums, sorted(name for version in newest[keep:] for name in versions[version])


def _order(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.split(r"[.-]", version))


def _publish(args) -> int:
    images = builder.ensure()
    image = _image_based(images, args.image)
    out = builder.CACHE / image.name
    channel = image_channel(image.name)
    sums = (out / "updates" / "SHA256SUMS").read_text()
    files = local(sums, out / "updates")
    # What a cell imports a new machine from, kept as long as its version is
    disk = f"{image.name}_{channel.version_of(next(iter(files)))}.{image.format}"
    files[disk] = out / f"{image.name}.{image.format}"
    with files[disk].open("rb") as stream:
        sums += f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {disk}\n"
    work = signing.prepare(images, builder.CACHE / "signing")
    publish(channel, out, work, sums, files)
    return 0


def _promote(args) -> int:
    images = builder.ensure()
    image = _image_based(images, args.image)
    out = builder.CACHE / image.name
    # A promotion builds nothing, so it makes the release key's keyring itself
    promote(image_channel(image.name), args.version, out, lambda: build.signer(images, image, "kms", out))
    return 0


def local(sums: str, directory: Path) -> dict[str, Path]:
    """Each file sums names, in directory."""
    return {name: directory / name for name in _names(sums)}


def publish(channel: Channel, out: Path, work: Path, sums: str, files: dict[str, Path]) -> None:
    """A release's files, each one sums names, added to the channel's canary."""
    _update(channel, out, work, "canary", sums, {name: str(path) for name, path in files.items()})


def promote(channel: Channel, version: str, out: Path, signer: Callable[[], Path]) -> None:
    """A version canary holds, copied to stable, its sums signed by the keyring signer makes."""
    canary = f"s3://{_bucket()}/{channel.prefix}/canary"
    sums = "".join(f"{line}\n" for line in _read(f"{canary}/SHA256SUMS").splitlines()
                   if line.strip() and channel.version_of(line.split(maxsplit=1)[1]) == version)
    if not sums:
        raise BootstrapError(f"{channel.prefix}'s canary has no {version}")
    _update(channel, out, signer(), "stable", sums, {name: f"{canary}/{name}" for name in _names(sums)})


def _update(channel: Channel, out: Path, work: Path, ring: str, sums: str, sources: dict[str, str]) -> None:
    """Adds sums' files to one of a channel's rings, from sources local or in the bucket, and prunes what it no
    longer holds. The files go first and the sums last, so a client never reads sums whose files aren't there."""
    prefix = f"s3://{_bucket()}/{channel.prefix}/{ring}"
    with tempfile.TemporaryDirectory(dir=builder.CACHE) as tmp:
        work_dir = Path(tmp)
        old = _read(f"{prefix}/SHA256SUMS")
        if old:
            if not (signature := _read(f"{prefix}/SHA256SUMS.gpg", binary=True)):
                raise BootstrapError(f"{prefix}/SHA256SUMS.gpg is missing, so the channel's sums can't be checked")
            (work_dir / "old").write_text(old)
            (work_dir / "old.gpg").write_bytes(signature)
        merged, pruned = merge(old, sums, channel)
        (work_dir / "SHA256SUMS").write_text(merged)
        builder.run_in({work_dir: "/work", out: "/out", **signing.mounts(work)}, "sh", "-c", SIGN, env=signing.env())
        say(f"publishing {', '.join(sorted({channel.version_of(n) for n in sources}))} to {prefix}")
        for name, source in sources.items():
            # Not the source's tags, the CLI's default for a copy within the bucket, which the release role may not read
            props = ["--copy-props", "metadata-directive"] if source.startswith("s3://") else []
            run("aws", "s3", "cp", "--only-show-errors", *props, source, f"{prefix}/{name}")
        for name in ("SHA256SUMS.gpg", "SHA256SUMS"):
            run("aws", "s3", "cp", "--only-show-errors", str(work_dir / name), f"{prefix}/{name}")
        for name in pruned:
            run("aws", "s3", "rm", "--only-show-errors", f"{prefix}/{name}")
        if pruned:
            say(f"pruned {', '.join(sorted({channel.version_of(n) for n in pruned}))} from {prefix}")


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
