#!/usr/bin/env bash
# Bake an unattended-install ISO from answer.toml.tmpl.
#
#   ./prepare-iso.sh proxmox-ve_9.0-1.iso [output.iso]
#
# Needs: docker, and either `op` signed in or OP_SERVICE_ACCOUNT_TOKEN set.
set -euo pipefail

SRC_ISO="${1:?usage: prepare-iso.sh <source.iso> [output.iso]}"
OUT_ISO="${2:-${SRC_ISO%.iso}-auto.iso}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="${HERE}/answer.toml.tmpl"

# Match the ISO you are baking. The assistant has to come from a Proxmox release at
# least as new as the installer it is preparing.
PVE_SUITE="${PVE_SUITE:-trixie}"
IMAGE="${IMAGE:-debian:${PVE_SUITE}-slim}"

for cmd in docker op; do
	command -v "${cmd}" >/dev/null || { echo "${cmd} not found" >&2; exit 1; }
done
[[ -f "${SRC_ISO}" ]] || { echo "no such ISO: ${SRC_ISO}" >&2; exit 1; }

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

echo "==> resolving secrets into ${workdir}/answer.toml"
op inject -i "${TEMPLATE}" -o "${workdir}/answer.toml"
chmod 0600 "${workdir}/answer.toml"

# Comments in the template mention op:// too, so only non-comment lines count.
if grep -v '^[[:space:]]*#' "${workdir}/answer.toml" | grep -q 'op://'; then
	echo "op inject left unresolved references - check the item and field names" >&2
	exit 1
fi

cp "${SRC_ISO}" "${workdir}/source.iso"

echo "==> preparing ISO in ${IMAGE}"
docker run --rm -i \
	-v "${workdir}:/work" \
	-w /work \
	-e DEBIAN_FRONTEND=noninteractive \
	"${IMAGE}" bash -euo pipefail -c "
		apt-get update -qq
		apt-get install -y -qq --no-install-recommends ca-certificates curl gnupg >/dev/null
		curl -fsSL -o /usr/share/keyrings/proxmox-archive-keyring.gpg \
			https://enterprise.proxmox.com/debian/proxmox-archive-keyring-${PVE_SUITE}.gpg
		cat > /etc/apt/sources.list.d/pve.sources <<-SOURCES
			Types: deb
			URIs: http://download.proxmox.com/debian/pve
			Suites: ${PVE_SUITE}
			Components: pve-no-subscription
			Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
		SOURCES
		apt-get update -qq
		apt-get install -y -qq --no-install-recommends proxmox-auto-install-assistant >/dev/null
		proxmox-auto-install-assistant validate-answer /work/answer.toml
		proxmox-auto-install-assistant prepare-iso /work/source.iso \
			--fetch-from iso --answer-file /work/answer.toml --output /work/output.iso
	"

mv "${workdir}/output.iso" "${OUT_ISO}"
echo "==> wrote ${OUT_ISO}"
