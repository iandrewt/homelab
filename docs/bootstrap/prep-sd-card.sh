#!/usr/bin/env bash
# Flashes the latest Debian raspi arm64 image to an SD card and drops a NoCloud
# cloud-init seed on its boot (ESP) partition.
set -euo pipefail

CODENAME="trixie"
TIMEZONE="Australia/Sydney"
DRY_RUN=false
ESP_TYPE_GUID="c12a7328-f81f-11d2-ba4b-00a0c93ec93b"

usage() {
  cat <<'EOF'
Usage: prep-sd-card.sh --device /dev/sdX --hostname NAME
                        (--ssh-key PATH | --ssh-key-op "op://vault/item/public key")
                        [--codename trixie] [--timezone TZ] [--dry-run]

  --device      Target block device, e.g. /dev/sdb. Check with lsblk first.
  --hostname    Hostname for the new host.
  --ssh-key     Path to a public key file to authorize on the image's own default
                "debian" user
  --ssh-key-op  1Password secret reference to read the public key from instead
                (needs the op CLI signed in, or OP_SERVICE_ACCOUNT_TOKEN set).
  --codename    Debian release codename. Defaults to trixie - always fetches
                whatever cloud.debian.org/images/cloud/<codename>/latest/ currently
                points at, not a pinned version.
  --timezone    Defaults to Australia/Sydney.
  --dry-run     Print what would happen, write nothing.
EOF
}

DEVICE=
HOSTNAME=
SSH_KEY_PATH=
SSH_KEY_OP=

while [[ $# -gt 0 ]]; do
  case "$1" in
    --device) DEVICE="$2"; shift 2 ;;
    --hostname) HOSTNAME="$2"; shift 2 ;;
    --ssh-key) SSH_KEY_PATH="$2"; shift 2 ;;
    --ssh-key-op) SSH_KEY_OP="$2"; shift 2 ;;
    --codename) CODENAME="$2"; shift 2 ;;
    --timezone) TIMEZONE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

for var in DEVICE HOSTNAME; do
  if [[ -z "${!var}" ]]; then
    echo "Missing required argument (see --help)" >&2
    exit 1
  fi
done
if { [[ -n "${SSH_KEY_PATH}" ]] && [[ -n "${SSH_KEY_OP}" ]]; } || { [[ -z "${SSH_KEY_PATH}" ]] && [[ -z "${SSH_KEY_OP}" ]]; }; then
  echo "Exactly one of --ssh-key or --ssh-key-op is required" >&2
  exit 1
fi

if [[ ! -b "${DEVICE}" ]]; then
  echo "${DEVICE} is not a block device" >&2
  exit 1
fi

ROOT_DISK=$(lsblk -no PKNAME "$(findmnt -no SOURCE /)" 2>/dev/null || true)
if [[ -n "${ROOT_DISK}" ]] && [[ "$(basename "${DEVICE}")" = "${ROOT_DISK}" ]]; then
  echo "Refusing: ${DEVICE} looks like the disk this system is running from." >&2
  exit 1
fi

echo "About to overwrite everything on ${DEVICE}:"
lsblk "${DEVICE}"
read -r -p "Type the device path to confirm (${DEVICE}): " CONFIRM
if [[ "${CONFIRM}" != "${DEVICE}" ]]; then
  echo "Confirmation didn't match, aborting." >&2
  exit 1
fi

if [[ -n "${SSH_KEY_OP}" ]]; then
  command -v op >/dev/null || { echo "op CLI not found" >&2; exit 1; }
  SSH_KEY_CONTENT=$(op read "${SSH_KEY_OP}")
else
  SSH_KEY_CONTENT=$(cat "${SSH_KEY_PATH}")
fi

BASE_URL="https://cloud.debian.org/images/cloud/${CODENAME}/latest"
SUMS=$(curl -fsSL "${BASE_URL}/SHA512SUMS")
TARBALL_LINE=$(grep 'raspi-arm64\.tar\.xz$' <<<"${SUMS}")
if [[ -z "${TARBALL_LINE}" ]]; then
  echo "No raspi-arm64.tar.xz entry found for codename '${CODENAME}' at ${BASE_URL}" >&2
  exit 1
fi
TARBALL_SHA=$(awk '{print $1}' <<<"${TARBALL_LINE}")
TARBALL_NAME=$(awk '{print $2}' <<<"${TARBALL_LINE}")
IMAGE_URL="${BASE_URL}/${TARBALL_NAME}"

if [[ "${DRY_RUN}" = true ]]; then
  echo "--dry-run: would fetch ${IMAGE_URL}, verify sha512, flash to ${DEVICE}, seed hostname=${HOSTNAME}"
  exit 0
fi

WORKDIR=$(mktemp -d)
trap 'rm -rf "${WORKDIR}"' EXIT

echo "Downloading ${IMAGE_URL}"
curl -fL -o "${WORKDIR}/${TARBALL_NAME}" "${IMAGE_URL}"

echo "Verifying checksum"
(cd "${WORKDIR}" && echo "${TARBALL_SHA}  ${TARBALL_NAME}" | sha512sum -c -)

echo "Extracting"
tar xJf "${WORKDIR}/${TARBALL_NAME}" -C "${WORKDIR}" disk.raw

echo "Writing to ${DEVICE}"
dd if="${WORKDIR}/disk.raw" of="${DEVICE}" bs=4M status=progress conv=fsync
partprobe "${DEVICE}" 2>/dev/null || true
udevadm settle

# GPT partition number varies by image (this one puts the ESP at 15, hybrid-MBR'd for
# Pi 3B's non-UEFI boot ROM) - find it by GPT partition type GUID instead of assuming
# a number.
BOOT_PART=$(lsblk -no PATH,PARTTYPE "${DEVICE}" | awk -v t="${ESP_TYPE_GUID}" 'tolower($2)==t {print $1}')
if [[ -z "${BOOT_PART}" ]]; then
  echo "Couldn't find the ESP partition by type GUID - check manually with lsblk." >&2
  exit 1
fi

MOUNT_POINT="${WORKDIR}/boot"
mkdir -p "${MOUNT_POINT}"
mount "${BOOT_PART}" "${MOUNT_POINT}"
trap 'umount "${MOUNT_POINT}" 2>/dev/null || true; rm -rf "${WORKDIR}"' EXIT

cat > "${MOUNT_POINT}/meta-data" <<EOF
instance-id: ${HOSTNAME}
local-hostname: ${HOSTNAME}
EOF

cat > "${MOUNT_POINT}/user-data" <<EOF
#cloud-config
hostname: ${HOSTNAME}
ssh_authorized_keys:
  - ${SSH_KEY_CONTENT}
ssh_pwauth: false
timezone: ${TIMEZONE}
EOF

sync
umount "${MOUNT_POINT}"
trap 'rm -rf "${WORKDIR}"' EXIT

echo "Done. Insert into the Pi and power on."
