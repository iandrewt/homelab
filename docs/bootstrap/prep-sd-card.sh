#!/usr/bin/env bash
# Flashes the latest Raspberry Pi OS Lite arm64 image to an SD card and drops a NoCloud
# cloud-init seed on its boot partition.
set -euo pipefail

TIMEZONE="Australia/Sydney"
DRY_RUN=false
IMAGES_BASE_URL="https://downloads.raspberrypi.com/raspios_lite_arm64/images"

usage() {
  cat <<'EOF'
Usage: prep-sd-card.sh --device /dev/sdX --hostname NAME
                        (--ssh-key PATH | --ssh-key-op "op://vault/item/public key")
                        [--timezone TZ] [--dry-run]

  --device      Target block device, e.g. /dev/sdb. Check with lsblk first.
  --hostname    Hostname for the new host.
  --ssh-key     Path to a public key file to authorize on the image's own default
                "pi" user.
  --ssh-key-op  1Password secret reference to read the public key from instead
                (needs the op CLI signed in, or OP_SERVICE_ACCOUNT_TOKEN set).
  --timezone    Defaults to Australia/Sydney.
  --dry-run     Print what would happen, write nothing.

Always fetches whatever downloads.raspberrypi.com/raspios_lite_arm64/images/ currently
lists as the latest dated build, not a pinned version.
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

# Dated subdirectories, e.g. raspios_lite_arm64-2026-06-19/ - lexicographic sort is
# chronological sort for ISO dates, so the last one is the latest.
LATEST_DIR=$(curl -fsSL "${IMAGES_BASE_URL}/" | grep -oE 'raspios_lite_arm64-[0-9]{4}-[0-9]{2}-[0-9]{2}/' | sort -u | tail -1)
if [[ -z "${LATEST_DIR}" ]]; then
  echo "Couldn't find any raspios_lite_arm64-* directories at ${IMAGES_BASE_URL}/" >&2
  exit 1
fi

IMAGE_NAME=$(curl -fsSL "${IMAGES_BASE_URL}/${LATEST_DIR}" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}-raspios-[a-z]+-arm64-lite\.img\.xz' | sort -u | head -1)
if [[ -z "${IMAGE_NAME}" ]]; then
  echo "Couldn't find a .img.xz image in ${IMAGES_BASE_URL}/${LATEST_DIR}" >&2
  exit 1
fi
IMAGE_URL="${IMAGES_BASE_URL}/${LATEST_DIR}${IMAGE_NAME}"

if [[ "${DRY_RUN}" = true ]]; then
  echo "--dry-run: would fetch ${IMAGE_URL}, verify sha256, flash to ${DEVICE}, seed hostname=${HOSTNAME}"
  exit 0
fi

WORKDIR=$(mktemp -d)
cleanup() {
  if [[ -n "${MOUNT_POINT:-}" ]] && mountpoint -q "${MOUNT_POINT}" 2>/dev/null; then
    sudo umount "${MOUNT_POINT}" 2>/dev/null || true
  fi
  rm -rf "${WORKDIR}"
}
trap cleanup EXIT

echo "Downloading ${IMAGE_URL}"
curl -fL -o "${WORKDIR}/${IMAGE_NAME}" "${IMAGE_URL}"
curl -fsSL -o "${WORKDIR}/${IMAGE_NAME}.sha256" "${IMAGE_URL}.sha256"

echo "Verifying checksum"
(cd "${WORKDIR}" && sha256sum -c "${IMAGE_NAME}.sha256")

echo "Extracting"
xz -d "${WORKDIR}/${IMAGE_NAME}"
IMAGE_RAW="${WORKDIR}/${IMAGE_NAME%.xz}"

echo "Writing to ${DEVICE}"
sudo dd if="${IMAGE_RAW}" of="${DEVICE}" bs=4M status=progress conv=fsync
sudo partprobe "${DEVICE}" 2>/dev/null || true
udevadm settle

# Classic MBR, boot is always the first partition.
BOOT_PART=$(lsblk -no PATH "${DEVICE}" | sed -n '2p')
if [[ -z "${BOOT_PART}" ]]; then
  echo "Couldn't find the boot partition - check manually with lsblk." >&2
  exit 1
fi

MOUNT_POINT="${WORKDIR}/boot"
mkdir -p "${MOUNT_POINT}"
# uid=/gid= makes the mounted FAT filesystem owned by the invoking user, so the
# meta-data/user-data writes below don't need sudo either.
_UID=$(id -u)
_GID=$(id -g)
sudo mount -o "uid=${_UID},gid=${_GID}" "${BOOT_PART}" "${MOUNT_POINT}"

cat > "${MOUNT_POINT}/meta-data" <<EOF
instance-id: ${HOSTNAME}
local-hostname: ${HOSTNAME}
EOF

cat > "${MOUNT_POINT}/user-data" <<EOF
#cloud-config
hostname: ${HOSTNAME}
enable_ssh: true
users:
  - default
ssh_authorized_keys:
  - ${SSH_KEY_CONTENT}
ssh_pwauth: false
timezone: ${TIMEZONE}
runcmd:
  - usermod --shell /bin/bash pi
EOF

sync
sudo umount "${MOUNT_POINT}"

echo "Done. Insert into the Pi and power on."
