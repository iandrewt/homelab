"""A Pi's card, as `bootstrap pi` writes one: an image-based release laid out at the card's size from its own
repart definitions, and the machine's identity as credentials on its ESP, which systemd-boot hands every stage."""

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
systemd-repart --dry-run=no --offline=yes --no-pager --architecture="$architecture" --seed="$seed" \
  --definitions=/tmp/usr/lib/repart.d "$card" >/dev/null
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


def lay_out(card: Path, size: str, machine_id: str, architecture: str) -> None:
    """The card at its size, with every partition its image's definitions name."""
    builder.run_in({card.parent: "/card"}, "sh", "-c", LAY_OUT, "lay-out", f"/card/{card.name}", size,
                   machine_id, architecture, USR[architecture])


def credentials(card: Path, credentials: Path) -> None:
    """Each file in credentials, named as the credential it is, on the card's ESP."""
    builder.run_in({card.parent: "/card", credentials: "/credentials:ro"}, "sh", "-c", CREDENTIALS,
                   "credentials", f"/card/{card.name}", "/credentials", ESP)
