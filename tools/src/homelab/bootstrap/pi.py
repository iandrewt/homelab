import argparse
import lzma
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import netbox, secrets
from ..model import load
from ..render import seed
from . import blockdev
from .download import CHUNK, fetch, get
from .shell import BootstrapError, require, run, say

IMAGES_BASE_URL = "https://downloads.raspberrypi.com/raspios_lite_arm64/images"

# Raspberry Pi OS ships `pi` pre-created with /usr/sbin/nologin, and its cloud.cfg has no
# top-level `users:` for cc_ssh to resolve a target from - so keys are silently dropped.
# Declaring it restores that; the shell needs fixing by hand because create_user()
# returns early for a name that already exists.
IMAGE_REPAIR = {
    "users": ["default"],
    "enable_ssh": True,
    "ssh_pwauth": False,
    "runcmd": ["usermod --shell /bin/bash pi"],
}

# rpi-cloud-init-mods makes NetworkManager netplan's default renderer in /usr/lib/netplan;
# a file of the same name in /etc/netplan shadows it.
NETPLAN_DEFAULT = (
    "etc/netplan/00-network-manager-all.yaml",
    "network:\n  version: 2\n  renderer: networkd\n",
)


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "pi",
        help="flash and seed a Raspberry Pi SD card",
        description="Writes the latest Raspberry Pi OS Lite arm64 build to a card and "
        "seeds it with an SSH key and network configuration.",
    )
    parser.add_argument(
        "--device", required=True, help="target block device, e.g. /dev/sdb"
    )
    parser.add_argument(
        "--hostname", required=True, help="written into meta-data as local-hostname"
    )
    parser.add_argument(
        "--site", required=True, help="slug of the site whose resolvers and zone to use"
    )
    key = parser.add_mutually_exclusive_group(required=True)
    key.add_argument("--ssh-key", type=Path, help="public key file to authorise")
    key.add_argument(
        "--ssh-key-ref", help="1Password reference to read public key file to authorise"
    )
    parser.add_argument("--user-data", type=Path, help="host-specific user-data")
    parser.add_argument(
        "--dry-run", action="store_true", help="print what would happen"
    )
    parser.set_defaults(run=main)


def main(args: argparse.Namespace) -> int:
    require("sudo", "dd", "lsblk", "mount", "umount", "chroot")

    try:
        model = load()
    except netbox.Unreachable as bad:
        raise BootstrapError(str(bad)) from None

    site = model["sites"].get(args.site)
    if site is None:
        known = ", ".join(sorted(model["sites"]))
        raise BootstrapError(f"{args.site!r} is not a site in NetBox. Known: {known}")

    device = Path(args.device)
    if not args.dry_run:
        _check_device(device)

    url = latest_image_url()

    if args.dry_run:
        say(f"would fetch {url}")
        say(f"would flash {args.device}, swap NetworkManager for systemd-networkd and "
            f"systemd-resolved, and seed hostname={args.hostname}")
        for name, body in documents(
            site, args.hostname, "<ssh key>", args.user_data
        ).items():
            print(f"--- {name} ---\n{body.decode(errors='replace')}")
        return 0

    _confirm(device)
    ssh_key = _ssh_key(args)

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        compressed = fetch(url, work / url.rsplit("/", 1)[-1], get(f"{url}.sha256").split()[0])
        _write(compressed, device)
        _bake(work, device)
        _seed(work, args, site, ssh_key, device)

    say("done - insert into the Pi and power on")
    return 0


def latest_image_url() -> str:
    """Dated directories sort chronologically, so the last one is the newest build."""
    listing = get(f"{IMAGES_BASE_URL}/")
    directories = sorted(
        set(re.findall(r"raspios_lite_arm64-\d{4}-\d{2}-\d{2}/", listing))
    )
    if not directories:
        raise BootstrapError(
            f"no raspios_lite_arm64-* directories at {IMAGES_BASE_URL}/"
        )

    latest = directories[-1]
    names = sorted(
        set(
            re.findall(
                r"\d{4}-\d{2}-\d{2}-raspios-[a-z]+-arm64-lite\.img\.xz",
                get(f"{IMAGES_BASE_URL}/{latest}"),
            )
        )
    )
    if not names:
        raise BootstrapError(f"no .img.xz in {IMAGES_BASE_URL}/{latest}")
    return f"{IMAGES_BASE_URL}/{latest}{names[0]}"


def _write(compressed: Path, device: Path) -> None:
    say(f"writing to {device}")
    writer = subprocess.Popen(
        ["sudo", "dd", f"of={device}", "bs=4M", "status=progress", "conv=fsync"],
        stdin=subprocess.PIPE,
    )
    try:
        with lzma.open(compressed) as image:
            shutil.copyfileobj(image, writer.stdin, CHUNK)
    finally:
        writer.stdin.close()
        if writer.wait():
            raise BootstrapError(f"writing to {device} failed")

    subprocess.run(["sudo", "partprobe", str(device)], check=False, capture_output=True)
    subprocess.run(["udevadm", "settle"], check=False)


def _bake(work: Path, device: Path) -> None:
    """Puts the image on the network stack every other host runs, before it first boots.

    networkd hands DNS to resolved and nothing else, and resolved isn't in the image - so
    a Pi that booted on networkd first could never fetch it.
    """
    say("replacing NetworkManager with systemd-networkd and systemd-resolved")
    root = work / "root"
    root.mkdir()
    table = run("lsblk", "-no", "PATH,FSTYPE", str(device), capture=True)
    run("sudo", "mount", str(blockdev.root_partition(device, table)), str(root))
    try:
        run("sudo", "mount", "-t", "proc", "proc", str(root / "proc"))
        run("sudo", "mount", "--bind", "/sys", str(root / "sys"))
        run("sudo", "mount", "--bind", "/dev", str(root / "dev"))
        try:
            _in_image(root, "true")
        except BootstrapError:
            raise BootstrapError(
                "can't run the image's arm64 binaries: this needs a static qemu-aarch64 "
                "registered with binfmt_misc (Arch: qemu-user-static-binfmt)"
            ) from None

        resolv = root / "etc/resolv.conf"
        run("sudo", "cp", "--remove-destination", "/etc/resolv.conf", str(resolv))
        _in_image(root, "apt-get", "update")
        _in_image(root, "apt-get", "install", "-y", "systemd-resolved")
        _in_image(
            root, "apt-get", "purge", "-y", "--autoremove",
            "network-manager", "avahi-daemon", "libnss-mdns",
        )
        _in_image(root, "apt-get", "clean")
        run("sudo", "ln", "-sfn", "../run/systemd/resolve/stub-resolv.conf", str(resolv))

        path, body = NETPLAN_DEFAULT
        run("sudo", "install", "-m", "0600", "/dev/stdin", str(root / path), input=body)
    finally:
        run("sudo", "umount", "-R", str(root))


def _in_image(root: Path, *command: str) -> None:
    run(
        "sudo", "chroot", str(root), "/usr/bin/env",
        "PATH=/usr/sbin:/usr/bin:/sbin:/bin", "DEBIAN_FRONTEND=noninteractive",
        *command,
    )


def _check_device(device: Path) -> None:
    if not device.is_block_device():
        raise BootstrapError(f"{device} is not a block device")
    running_from = blockdev.root_disks()
    if blockdev.backing_disks(device) & running_from:
        raise BootstrapError(
            f"refusing: {device} is backed by {', '.join(sorted(running_from))}, "
            "which this system is running from"
        )


def _confirm(device: Path) -> None:
    print(f"About to overwrite everything on {device}:")
    run("lsblk", str(device))
    if input(f"Type the device path to confirm ({device}): ").strip() != str(device):
        raise BootstrapError("confirmation didn't match")


def _ssh_key(args) -> str:
    if args.ssh_key_ref:
        return secrets.resolve_one(args.ssh_key_ref).strip()
    return args.ssh_key.read_text().strip()


def _seed(work: Path, args, site, ssh_key: str, device: Path) -> None:
    mount = work / "boot"
    mount.mkdir()
    # uid=/gid= so the writes below don't need sudo as well.
    table = run("lsblk", "-no", "PATH,FSTYPE", str(device), capture=True)
    run(
        "sudo",
        "mount",
        "-o",
        f"uid={os.getuid()},gid={os.getgid()}",
        str(blockdev.boot_partition(device, table)),
        str(mount),
    )
    try:
        for name, body in documents(site, args.hostname, ssh_key, args.user_data).items():
            (mount / name).write_bytes(body)
        os.sync()
    finally:
        run("sudo", "umount", str(mount))


def documents(
    site, hostname: str, ssh_key: str, user_data: Path | None
) -> dict[str, bytes]:
    return {
        "meta-data": seed.meta_data(hostname, site["slug"], ssh_keys=[ssh_key]).encode(),
        "vendor-data": seed.cloud_config(IMAGE_REPAIR).encode(),
        "user-data": user_data.read_bytes() if user_data else b"",
        "network-config": seed.network_config(
            site, resolver=site["resolvers"].get(hostname)
        ).encode(),
    }
