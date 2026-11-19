"""A Pi's card: an image-based release from its update channel, checked against the release key, laid out whole at
the card's size, and the machine's identity as credentials on its ESP."""

import argparse
import os
import re
import subprocess
import tempfile
import urllib.request
import uuid
from pathlib import Path

from .. import netbox
from ..image import builder, card
from ..model import load
from ..render import seed
from ..repo import Repo
from . import blockdev
from .download import fetch, get
from .shell import BootstrapError, require, run, say

IMAGE = "ymir-arm64-raspi3"
ARCHITECTURE = "arm64"
# A machine ID is its board's: the same serial number makes the same ID, as a VM's is its SMBIOS UUID
MACHINE_IDS = uuid.UUID("9b6a1a2e-4c1e-4f3e-9d5f-6f1c0a7e2b11")

# Signed by the release key, by its fingerprint, as a machine's sysupdate checks it
UPDATES_KEY = "6D13C27C5938E41A486FD72D735765B2A9C3EEB7"
CHECK = f"""
set -eu
export GNUPGHOME=$(mktemp -d)
gpg --batch --quiet --import /keys/updates.pgp 2>/dev/null
gpg --batch --status-fd 1 --verify /work/SHA256SUMS.gpg /work/SHA256SUMS 2>/dev/null | grep -q "^\\[GNUPG:\\] VALIDSIG {UPDATES_KEY} "
"""


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "pi", help="write a Raspberry Pi's card",
        description=f"Writes the newest {IMAGE} release in a channel to a card at its own size, every partition made, "
        "its identity as credentials on its ESP: its machine ID from its serial number in NetBox, its hostname, the "
        "key, and its network, a resolver's own resolvers pinned. The channel is IMAGES_UPDATE_URL's.",
    )
    parser.add_argument("--device", required=True, help="the card, e.g. /dev/sdb")
    parser.add_argument("--hostname", required=True, help="the Pi's device in NetBox, e.g. ratatoskr")
    parser.add_argument("--site", required=True, help="slug of its site")
    key = parser.add_mutually_exclusive_group(required=True)
    key.add_argument("--ssh-key", type=Path, help="public key file to authorise")
    key.add_argument("--ssh-key-ref", help="1Password reference to the public key to authorise")
    parser.add_argument("--channel", default="stable", choices=("stable", "canary"))
    parser.add_argument("--version", help="a version the channel holds; its newest by default")
    parser.add_argument("--dry-run", action="store_true", help="check the release and show the credentials, and write nothing")
    parser.set_defaults(run=main)


def main(args: argparse.Namespace) -> int:
    require("sudo", "dd", "blockdev", "lsblk")
    if not (base := os.environ.get("IMAGES_UPDATE_URL")):
        raise BootstrapError("IMAGES_UPDATE_URL names the update service: platform/aws/images's output `update_url`")
    try:
        snap = netbox.fetch()
    except netbox.Unreachable as bad:
        raise BootstrapError(str(bad)) from None
    site = load(snap)["sites"].get(args.site)
    if site is None:
        raise BootstrapError(f"{args.site!r} is not a site in NetBox")
    device = Path(args.device)
    if not args.dry_run:
        _check_device(device)

    channel = f"{base.rstrip('/')}/{IMAGE}/{args.channel}"
    out = builder.CACHE / IMAGE / "card"
    out.mkdir(parents=True, exist_ok=True)
    name, digest = _release(channel, out, args.version)
    say(f"{name} from {args.channel}, its sums signed by the release key")
    credentials = {
        "system.machine_id": machine_id(snap, args.site, args.hostname),
        "system.hostname": args.hostname,
        "ssh.authorized_keys.debian": _ssh_key(args),
        "network.network.50-seed": seed.network_unit(site, resolver=site["dns"]["servers"].get(args.hostname)),
    }
    if args.dry_run:
        for credential, value in credentials.items():
            print(f"--- {credential} ---\n{value.rstrip()}")
        return 0

    _confirm(device)
    fetch(f"{channel}/{name}", out / name, digest)
    size = run("sudo", "blockdev", "--getsize64", str(device), capture=True).strip()
    with tempfile.TemporaryDirectory(dir=builder.CACHE) as tmp:
        work = Path(tmp)
        say(f"laying it out at {int(size) >> 30}G")
        builder.run_in({out: "/release:ro", work: "/work"}, "sh", "-c",
                       f"xz -dc /release/{name} | dd of=/work/card.raw bs=4M conv=sparse status=none")
        card.lay_out(work / "card.raw", size, credentials["system.machine_id"], ARCHITECTURE)
        (work / "credentials").mkdir()
        for credential, value in credentials.items():
            (work / "credentials" / credential).write_text(value)
        card.credentials(work / "card.raw", work / "credentials")
        _write(work / "card.raw", device)
    say(f"done: its machine ID is {credentials['system.machine_id']}; pin its new host key once it's up")
    return 0


def machine_id(snap: netbox.Snapshot, site: str, hostname: str) -> str:
    """The Pi's machine ID, from its serial number in NetBox: a reflash keeps it, a new board is a new machine."""
    for raw in snap.devices:
        if raw["name"] == hostname and (raw.get("site") or {}).get("slug") == site:
            if not raw.get("serial"):
                raise BootstrapError(f"{hostname} has no serial number in NetBox: its board's, from `cat "
                                     "/sys/firmware/devicetree/base/serial-number` or the label")
            return uuid.uuid5(MACHINE_IDS, raw["serial"].strip().lower()).hex
    raise BootstrapError(f"{hostname} is not a device at {site} in NetBox")


def _release(channel: str, out: Path, version: str | None) -> tuple[str, str]:
    """A card image the channel's signed sums name, and its digest: the version asked for, or the newest."""
    (out / "SHA256SUMS").write_text(get(f"{channel}/SHA256SUMS"))
    with urllib.request.urlopen(f"{channel}/SHA256SUMS.gpg") as response:
        (out / "SHA256SUMS.gpg").write_bytes(response.read())
    builder.run_in({out: "/work:ro", Repo.discover().root / "images" / "secure-boot": "/keys:ro"}, "sh", "-c", CHECK)
    images = {}
    for line in (out / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        if match := re.fullmatch(rf"{re.escape(IMAGE)}_(.+)\.raw\.xz", name.lstrip("*")):
            images[match[1]] = (name.lstrip("*"), digest)
    if not images:
        raise BootstrapError(f"{channel} holds no card image")
    wanted = version or max(images, key=lambda v: tuple(int(p) for p in v.split(".")))
    if wanted not in images:
        raise BootstrapError(f"{channel} holds {', '.join(sorted(images))}, not {wanted}")
    return images[wanted]


def _write(image: Path, device: Path) -> None:
    """Only what the image holds: the card discarded first, so what it doesn't reads back as zeros where the card
    supports it."""
    say(f"writing to {device}")
    subprocess.run(["sudo", "blkdiscard", "--force", str(device)], check=False, capture_output=True)
    run("sudo", "dd", f"if={image}", f"of={device}", "bs=4M", "conv=sparse,fsync", "status=progress")
    subprocess.run(["sudo", "partprobe", str(device)], check=False, capture_output=True)


def _check_device(device: Path) -> None:
    if not device.is_block_device():
        raise BootstrapError(f"{device} is not a block device")
    running_from = blockdev.root_disks()
    if blockdev.backing_disks(device) & running_from:
        raise BootstrapError(f"refusing: {device} is backed by {', '.join(sorted(running_from))}, "
                             "which this system is running from")


def _confirm(device: Path) -> None:
    print(f"About to overwrite everything on {device}:")
    run("lsblk", str(device))
    if input(f"Type the device path to confirm ({device}): ").strip() != str(device):
        raise BootstrapError("confirmation didn't match")


def _ssh_key(args) -> str:
    if args.ssh_key_ref:
        from .. import secrets
        return secrets.resolve_one(args.ssh_key_ref).strip()
    return args.ssh_key.read_text().strip()
