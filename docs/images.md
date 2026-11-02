# Images

Platform's base images are hardened Debian, built from [`images/`](../images), for VMs on
Proxmox. This is what one gives a VM, what booting one takes, and how to make an image of your
own from one.

## What's in one

`debian-13-amd64-vm` is Debian 13 with its cloud kernel, the packages in its
[`mkosi.conf`](../images/debian-13-amd64-vm/mkosi.conf), and:

- **CIS Debian 13**, levels 1 and 2, with the exceptions in
  [`homelab.debian.hardening`'s defaults](../ansible/collections/ansible_collections/homelab/debian/roles/hardening/defaults/main.yml)
  and [its profile's](../ansible/baseline/group_vars/image_debian_13_amd64_vm.yml).
- **Key-only SSH**, as the default user, `debian`, who has passwordless sudo.
- **Unattended upgrades** from Debian's security archive and point releases. A new kernel waits
  for your reboot.
- **The QEMU guest agent**, allowing only what a Proxmox node calls of it, and reading files
  ([its list](../ansible/collections/ansible_collections/homelab/linux/roles/guest_agent/defaults/main.yml)).
- **DHCPv4 and SLAAC**, taking the lease's search domains.
- **UTC.** A schedule meant in local time names its zone: `OnCalendar=*-*-* 03:00 Australia/Sydney`.
- **Its firewall is Proxmox's**, the VM's own. nftables inside it is yours to add;
  [`homelab.linux.host_firewall`](../ansible/collections/ansible_collections/homelab/linux/roles/host_firewall)
  is one way.
- **`/usr/lib/homelab/image-release`**: which image it is (`IMAGE_ID`, `IMAGE_VERSION`), the
  commit that built it (`BUILD_ID`), and the versions of the collections that hardened it.

Its disk is laid out for CIS, by [these definitions](../images/debian-13-amd64-vm/mkosi.extra/usr/lib/repart.d):

| Mount | Size | Options |
| --- | --- | --- |
| `/efi` | 512 MiB | |
| `/` | 4 GiB | |
| `/home` | 1 GiB | `nodev,nosuid` |
| `/var/tmp` | 1 GiB | `nodev,nosuid,noexec` |
| `/var/log` | 2 GiB | `nodev,nosuid,noexec` |
| `/var/log/audit` | 1 GiB | `nodev,nosuid,noexec` |
| `/var` | the rest of the disk | `nodev,nosuid` |

**`/var` grows to fill whatever disk the VM is given**, on its first boot. Everything else is
fixed, `/opt` and `/srv` included: data belongs in `/var/lib`, or on a disk of its own.

## Getting one

Each is a release of this repo, tagged `image/<profile>/<version>`: the image, its package
manifest, and `SHA256SUMS`, signed keylessly by the workflow that built it. Check the signature
names [`images.yml`](../.github/workflows/images.yml) at that tag, then the files against the sums:

```
tag=image/debian-13-amd64-vm/2026.11.0
gh release download "$tag" --repo iandrewt/homelab
cosign verify-blob SHA256SUMS --bundle SHA256SUMS.sigstore.json \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity "https://github.com/iandrewt/homelab/.github/workflows/images.yml@refs/tags/$tag"
sha256sum --check SHA256SUMS
```

Or build one: `uv run homelab image build debian-13-amd64-vm`, then `uv run homelab image test
debian-13-amd64-vm` to check it.

## Booting one

**Give it an EFI disk with an empty key store** (in Proxmox, `pre-enrolled-keys=0`). Its first
boot finds the firmware in setup mode and enrolls [Platform's keys and Debian's
CA](../images/secure-boot), then resets; from then on it boots with Secure Boot enforcing, through
Debian-signed systemd-boot and kernels. It trusts Debian's signatures and Platform's keys alone.

**Give it a seed**, which is how you log in: a NoCloud drive, an ISO labelled `cidata`, holding
`meta-data` and `user-data` (which may be empty), and optionally `network-config`.

```yaml
# meta-data
instance-id: web-01-2026-11-03
local-hostname: web-01
public-keys:
  - ssh-ed25519 AAAA... you@example
```

**A new `instance-id` is a new instance**: new SSH host keys, a new machine ID. Change it only
to make the VM someone new. Its host key can be read before you trust it, through Proxmox's API
(`agent/file-read` of `/etc/ssh/ssh_host_ed25519_key.pub`), since the guest agent allows reading.

## An image of your own

**Derive it**: boot one of ours as a new VM, provision it, seal it. Its hardening comes with it,
and everything you add is yours.

```
uv run homelab image derive debian-13-amd64-vm.qcow2 web.qcow2 --playbook web.yml
uv run homelab image derive debian-13-amd64-vm.qcow2 web.qcow2 --script web.sh
```

A playbook runs from its own directory, against the VM as host `build`, reached as `debian`; a
script runs on it as root. Then [the base's seal](../images/shared/usr/lib/homelab/seal) undoes
what the boot did, so your image's every VM is a new instance, and `web.qcow2` is written.

```sh
#!/bin/sh
set -eu
apt-get update
apt-get install -y nginx
mkdir -p /usr/lib/web
printf 'IMAGE_ID=web\nIMAGE_VERSION=1\n' > /usr/lib/web/image-release
```

- **Name your image beside ours**, in a release file of your own, and leave
  `/usr/lib/homelab/image-release` as it is: it's how anyone tells which base yours is on.
- **To restore our hardening over your changes**, run
  [`homelab.debian`](../ansible/collections/ansible_collections/homelab/debian) and
  [`homelab.linux`](../ansible/collections/ansible_collections/homelab/linux) at the versions our
  release file names.
- **Check it keeps the base's contract**: `uv run homelab image test debian-13-amd64-vm --image
  web.qcow2` boots it twice as new VMs, and checks what this page says.
- **Another kernel or layout** is a profile of its own: copy one in `images/` and build it, with
  `uv run homelab image build <profile>`.

`derive` is one way; anything that boots the disk with a `cidata` drive works, as long as
`/usr/lib/homelab/seal` is the last thing it runs before powering off.
