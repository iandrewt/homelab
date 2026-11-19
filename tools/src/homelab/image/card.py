"""A Pi's card, as `bootstrap pi` writes one: an image-based release laid out at the card's size from its own
repart definitions, and the machine's identity as credentials on its ESP, which systemd-boot hands every stage.
And a test's: booted on the board qemu emulates, which has no network, its checks reported on the console."""

import re
import uuid
from pathlib import Path

from . import builder

# Each architecture's /usr, as the Discoverable Partitions Specification types it
USR = {"arm64": "B0E01050-EE5F-4390-949A-9101B17104E9"}
ESP = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"

PARTITION = """
part() { # a partition's type: its start and size, in sectors
  sfdisk --json "$1" | python3 -c 'import json, sys
for p in json.load(sys.stdin)["partitiontable"]["partitions"]:
    if p["type"].upper() == sys.argv[1]: print(p["start"], p["size"]); break' "$2"
}
"""

# Every partition made offline: nothing repartitions a card at boot, so the hybrid MBR its boot ROM reads
# stays, and a first boot formats nothing
LAY_OUT = PARTITION + """
set -eu
card=$1 size=$2 seed=$3 architecture=$4 usr=$5
truncate -s "$size" "$card"
sfdisk --quiet --relocate gpt-bak-std "$card"
set -- $(part "$card" "$usr")
dd if="$card" of=/tmp/usr.erofs bs=512 skip="$1" count="$2" status=none
fsck.erofs --extract=/tmp/usr /tmp/usr.erofs >/dev/null
# What /var starts with, where one's given
if [ -d /var-tree ]; then
  chown -R root:root /var-tree
  printf 'CopyFiles=/var-tree:/\n' >> /tmp/usr/lib/repart.d/60-var.conf
fi
systemd-repart --dry-run=no --offline=yes --no-pager --architecture="$architecture" --seed="$seed" \
  --copy-source=/ --definitions=/tmp/usr/lib/repart.d "$card" >/dev/null
"""

# The null key: nothing on a Pi is measured, so nothing could seal them, and none is a secret
CREDENTIALS = PARTITION + """
set -eu
card=$1 credentials=$2 esp=$3
set -- $(part "$card" "$esp")
at="$card@@$(($1 * 512))"
mmd -i "$at" ::loader/credentials 2>/dev/null || true
for credential in "$credentials"/*; do
  name=${credential##*/}
  systemd-creds encrypt --with-key=null --name="$name" "$credential" "/tmp/$name.cred" 2>/dev/null
  mcopy -o -i "$at" "/tmp/$name.cred" "::loader/credentials/$name.cred"
done
"""


def lay_out(card: Path, size: str, machine_id: str, architecture: str, var: Path | None = None) -> None:
    """The card at its size, with every partition its image's definitions name, and var's tree in /var."""
    builder.run_in({card.parent: "/card", **({var: "/var-tree"} if var else {})}, "sh", "-c", LAY_OUT, "lay-out",
                   f"/card/{card.name}", size, machine_id, architecture, USR[architecture])


def credentials(card: Path, credentials: Path) -> None:
    """Each file in credentials, named as the credential it is, on the card's ESP."""
    builder.run_in({card.parent: "/card", credentials: "/credentials:ro"}, "sh", "-c", CREDENTIALS,
                   "credentials", f"/card/{card.name}", "/credentials", ESP)


# Run once it's booted and any counted entry blessed, a line each on the console, then the end
CHECKS_UNIT = """[Unit]
After=multi-user.target boot-complete.target systemd-bless-boot.service

[Service]
Type=oneshot
StandardOutput=journal+console
ImportCredential=homelab.checks
ExecStart=/bin/sh ${CREDENTIALS_DIRECTORY}/homelab.checks
"""
CHECK = """check() { if eval "$2" >/dev/null 2>&1; then echo "homelab-check ok $1"; else echo "homelab-check FAIL $1"; fi; }
"""
END = "homelab-checks-end"

# Booted by its own U-Boot, as the Pi's firmware loads it, until the checks report on the mini UART, qemu's second
# serial port; with "next", a counted next version beside the first, as sysupdate installs one
BOOT = PARTITION + """
set -eu
cd /work
esp=$1 board=$2 rounds=$3 next=$4
set -- $(part card.raw "$esp")
at="card.raw@@$(($1 * 512))"
mcopy -o -i "$at" ::u-boot.bin ::bcm2710-rpi-3-b.dtb .
if [ "$next" = next ]; then
  uki=$(mdir -b -i "$at" ::EFI/Linux | head -n 1)
  mcopy -o -i "$at" "$uki" next.efi
  mcopy -o -i "$at" next.efi "::EFI/Linux/$(basename "$uki" .efi).1+3.efi"
  rm next.efi
fi
qemu-system-aarch64 -M "$board" -m 1G -kernel u-boot.bin -dtb bcm2710-rpi-3-b.dtb \
  -drive file=card.raw,if=sd,format=raw -display none -monitor none -serial null -serial file:serial.log &
qemu=$!
for _ in $(seq "$rounds"); do
  if grep -aq """ + END + """ serial.log 2>/dev/null || ! kill -0 "$qemu" 2>/dev/null; then break; fi
  sleep 5
done
kill "$qemu" 2>/dev/null || true
"""


def checked(work: Path, card_image: Path, architecture: str, board: str, checks: str, size: str, patience: int,
            var: Path | None = None, next_version: bool = False) -> tuple[list[tuple[bool, str]], bool]:
    """A card of the image, laid out with var's tree in /var, its identity a fresh machine ID and the hostname
    `test`, booted until its checks report: each check's verdict, and whether they ran to their end. @MACHINE_ID@
    in checks is the machine's."""
    machine_id = uuid.uuid4().hex
    seeds = work / "credentials"
    seeds.mkdir()
    (seeds / "system.machine_id").write_text(machine_id)
    (seeds / "system.hostname").write_text("test")
    (seeds / "homelab.checks").write_text(CHECK + checks.replace("@MACHINE_ID@", machine_id) + f"echo {END}\n")
    (seeds / "systemd.extra-unit.homelab-checks.service").write_text(CHECKS_UNIT)
    (seeds / "systemd.unit-dropin.multi-user.target").write_text("[Unit]\nWants=homelab-checks.service\n")
    builder.run_in({card_image.parent: "/image:ro", work: "/work"}, "cp", "--sparse=always",
                   f"/image/{card_image.name}", "/work/card.raw")
    lay_out(work / "card.raw", size, machine_id, architecture, var)
    credentials(work / "card.raw", seeds)
    builder.run_in({work: "/work"}, "sh", "-c", BOOT, "boot", ESP, board, str(patience // 5),
                   "next" if next_version else "none")
    log = (work / "serial.log").read_text(errors="replace")
    verdicts = [(verdict == "ok", what.strip()) for verdict, what in re.findall(r"homelab-check (ok|FAIL) (.*)", log)]
    return verdicts, END in log
