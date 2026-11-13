# Images

Platform's base images are hardened Debian, built from [`images/`](../images), one image per
kind of machine. This is what one gives a host, what booting one takes, and how to make an image
of your own from one.

| Image | For | Kernel | Boots through | Its seed |
| --- | --- | --- | --- | --- |
| [`debian-13-amd64-vm`](../images/mkosi.conf.d/mutable-vm) | a VM on Proxmox | Debian's cloud kernel | UEFI, Secure Boot with Platform's keys | a drive labelled `cidata` |
| [`debian-13-raspi3`](../images/mkosi.conf.d/mutable-raspi3.conf) | a Raspberry Pi 3B | Debian's arm64 kernel | `raspi-firmware` | its own partition, labelled `CIDATA` |
| [`ymir-amd64-vm`](../images/mkosi.conf.d/ymir-vm.conf) | a Platform appliance VM | Debian's cloud kernel | UEFI, Secure Boot with Platform's keys, from a signed UKI | Proxmox's cloud-init drive, which it reads itself |

**`ymir-amd64-vm` is image-based**: its `/usr`, and the `/etc` it starts from, are one signed,
verity-checked image, updated whole and A/B by systemd-sysupdate, rolled back if a version can't
boot. A machine keeps only its own state, encrypted to its vTPM, and its writes to `/etc` override the
image's. Nothing installs on it: what it runs comes as a signed system extension. It carries nftables,
for a host's own firewall, and Python with cryptography, for Ansible to configure it. It starts on a
4 GB disk, and `/var` takes whatever more its disk is grown by. A release's UKI, `/usr` and update
sums are signed by Platform's release key, which only a release can use, and which db trusts by
[its certificate](../images/secure-boot/db.crt).

**It updates from Platform's update service**, which admits the sites' public addresses alone: each
release goes to a `canary` channel, and is promoted to `stable`, which a machine follows. A version's
qcow2 is beside its update files, `<image>/<channel>/<image>_<version>.qcow2`, for a new machine to
start from, until its channel keeps five newer.
`systemd-sysupdate-update.timer` installs the newest of the image and of every component the machine
has enabled, and `systemd-sysupdate-reboot.timer` reboots into a new version of the image daily at
04:10 UTC, which a drop-in moves to a window of its own. A machine
following canary says so in `/etc/sysupdate.d/`, a copy of each `/usr/lib/sysupdate.d/*.transfer`
with `stable` in its `Path=` changed to `canary`.

**ymir has no cloud-init.** It reads the drive Proxmox writes from a VM's cloud-init settings
itself: the user-data's `hostname` names it, unless it has named itself in `/etc/hostname`, and its
`ssh_authorized_keys` let you in as `debian`, once; any other user fails the seed. Each NIC the
network-config names becomes networkd's own `50-seed-<name>.network`, matched by MAC, from the first
boot: DHCPv4 or a static address, SLAAC, and the drive's nameservers in place of the lease's, keeping
the lease's search domains. Proxmox gives a VM that names no nameservers its node's. A host's netplan
sorts before the seed's units, and a NIC the drive doesn't name takes DHCPv4 and SLAAC. The rest of
this page is the package-managed images', but for [its system extensions](#system-extensions).

## System extensions

What a ymir machine runs comes as a system extension: one workload's packages, built over the
image's `/usr` and holding only what they add, its verity signed by the release key.
[`images/extensions.toml`](../images/extensions.toml) lists them, and the images each is built over:

| Extension | Packages | From |
| --- | --- | --- |
| `pdns` | PowerDNS Authoritative, with LMDB | `repo.powerdns.com`, the 5.1 train |
| `pdns-recursor` | PowerDNS Recursor | `repo.powerdns.com`, the 5.4 train |
| `dnsdist` | dnsdist | `repo.powerdns.com`, the 2.1 train |
| `frr` | FRR | `deb.frrouting.org`, the 10.7 train |
| `haproxy` | HAProxy | Debian, at the image's snapshot |

**Each is a sysupdate component, disabled until a machine takes it**, and then updated on its own
version, from the same update service as the image:

```
sudo /usr/lib/systemd/systemd-sysupdate enable-component pdns-recursor
sudo /usr/lib/systemd/systemd-sysupdate --component=pdns-recursor update
```

It's merged at the next boot (or `systemd-sysext refresh`) into `/usr`, under
`/var/lib/extensions/<extension>.raw.v/`, and only if db trusts its signature. **Merged isn't
running**: what a package's scripts would write in `/etc` and `/var` isn't there. Its users and
their groups come with it as `sysusers.d`, and its packages' directories in `/var` as `tmpfiles.d`,
but none of its packages' `/etc` does, and nothing is enabled until the machine writes its own
configuration and enables the unit. A newer version is merged as it's installed, and a running
service keeps the one it started with until it restarts.

**A version is upstream's and a rebuild count**, `5.4.9-2`, as Arch's `pkgrel`. It's rebuilt
only when [`images/extensions.lock`](../images/extensions.lock) moves: what apt would add over the
image, every bundled library at its version, and the hash of what its build reads. What it takes
from the base moves with the base, whose ABI Debian holds within a release, and an extension names
the release it was built for (`ID=debian`, `VERSION_ID=13`), so systemd refuses it on another. A
library it bundles is a copy, which only a rebuild updates.

Each is released by a tag, `extension/<extension>/<version>`, as an image is: tested on the
image's newest release, published to `canary`, promoted to `stable` by a dispatch at its tag. Its
release on GitHub holds each image's extension and package list, and `SHA256SUMS`, signed as an
image's. Adding one is a profile in `images/mkosi.profiles/<extension>/` (its packages, and its
vendor's source signed by a key in `images/vendors/`), its entry in `images/extensions.toml`, and
`uv run homelab image extension lock`. Its component reaches machines with the image's next
release.

```
uv run homelab image extension lock --check   # whether any would be rebuilt
uv run homelab image extension build pdns-recursor
uv run homelab image extension test ymir-amd64-vm   # boots the image's build, takes each, checks it links
```

## What's in one

Debian 13, as [its distribution](../images/mkosi.conf.d/debian) has it, baked once per
architecture and shared by every image of it, with the image's family and target laid over. Every
package is from one of Debian's snapshots, its `Snapshot=`, bumped weekly, so a release is what its
commit says; its `.packages` lists them. Installed, its apt is plain `deb.debian.org`:

- **CIS Debian 13**, levels 1 and 2, with the exceptions in
  [`homelab.debian.hardening`'s defaults](../ansible/collections/ansible_collections/homelab/debian/roles/hardening/defaults/main.yml),
  every image's in [`images.yml`](../ansible/baseline/group_vars/images.yml), and its
  architecture's and its own beside it.
- **AIDE**, checking its files daily by a timer, reporting to the journal; on amd64, since a
  Pi's card can't afford its scan. No MTA, and no cron.
- **Key-only SSH**, as the default user, `debian`, who has passwordless sudo.
- **Unattended upgrades** from Debian's security archive and point releases. A new kernel waits
  for your reboot.
- **On a VM, the QEMU guest agent**, allowing only what a Proxmox node calls of it, and reading
  files ([its list](../images/mkosi.profiles/vm/mkosi.extra/etc/qemu/qemu-ga.conf)).
- **DHCPv4 and SLAAC**, taking the lease's search domains.
- **UTC.** A schedule meant in local time names its zone: `OnCalendar=*-*-* 03:00 Australia/Sydney`.
- **A VM's firewall is Proxmox's**, the VM's own. nftables inside it is yours to add;
  [`homelab.linux.host_firewall`](../ansible/collections/ansible_collections/homelab/linux/roles/host_firewall)
  is one way, and metal's.
- **`/usr/lib/homelab/image-release`**: which image it is (`IMAGE_ID`, `IMAGE_VERSION`) and its
  family (`HOMELAB_FAMILY`), the commit that built it (`BUILD_ID`), and the versions of the
  collections that hardened it.

Its disk is laid out for CIS, the same on every image: on GPT by repart's
[definitions](../images/mkosi.conf.d/mutable-vm/mkosi.extra/usr/lib/repart.d), and on a Pi's MBR by
its [layout](../images/mkosi.profiles/raspi3/layout).

| Mount | Size | Options |
| --- | --- | --- |
| `/efi`, or a Pi's `/boot/firmware` | 512 MiB | |
| `/` | 4 GiB | |
| `/home` | 1 GiB | `nodev,nosuid` |
| `/var/tmp` | 1 GiB | `nodev,nosuid,noexec` |
| `/var/log` | 2 GiB | `nodev,nosuid,noexec` |
| `/var/log/audit` | 1 GiB | `nodev,nosuid,noexec` |
| `/var` | the rest of the disk | `nodev,nosuid` |

**`/var` grows to fill whatever disk it's given**, on its first boot. Everything else is
fixed, `/opt` and `/srv` included: data belongs in `/var/lib`, or on a disk of its own.

## Getting one

Each is a release of this repo, tagged `image/<image>/<version>`: the image, its package
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

### On Proxmox

**Give it an EFI disk with an empty key store** (in Proxmox, `pre-enrolled-keys=0`). Its first
boot finds the firmware in setup mode and enrolls [Platform's keys and Debian's
CA](../images/secure-boot), then resets; from then on it boots with Secure Boot enforcing, through
Debian-signed systemd-boot and kernels. It trusts Debian's signatures and Platform's keys alone.

**Give it a seed**, which is how you log in: Proxmox's own cloud-init drive, with your SSH keys,
`ip=dhcp` and `ip6=auto`. Proxmox names it after the VM. Elsewhere, a NoCloud drive, an ISO labelled
`cidata`, holding `meta-data` and `user-data` (which may be empty), and optionally `network-config`:

```yaml
# meta-data
instance-id: web-01-2026-11-03
local-hostname: web-01
public-keys:
  - ssh-ed25519 AAAA... you@example
```

**A new `instance-id` is a new instance**: a new machine ID, and whatever cloud-init runs once per
instance. Proxmox's changes with a VM's keys or network. The SSH host keys stay: the image keeps them
(`ssh_deletekeys: false`). Its host key can be read before you trust it, through Proxmox's API
(`agent/file-read` of `/etc/ssh/ssh_host_ed25519_key.pub`), since the guest agent allows reading.

### On a Raspberry Pi 3B

**Write it to a card**, from its release, which ships it raw and compressed:

```
xz --decompress --stdout debian-13-raspi3-2026.11.0.raw.xz | sudo dd of=/dev/sdX bs=4M conv=fsync
```

**Put its seed on the card's partition labelled `CIDATA`**, the firmware partition: the same
`meta-data` and `user-data` as above, as files. `raspi-firmware` boots it from the same partition,
and `/var` grows to the end of the card on its first boot.

## An image of your own

**Derive it**: boot one of ours as a new VM, provision it, seal it. Its hardening comes with it,
and everything you add is yours.

```
uv run homelab image derive debian-13-amd64-vm debian-13-amd64-vm.qcow2 web.qcow2 --playbook web.yml
uv run homelab image derive debian-13-amd64-vm debian-13-amd64-vm.qcow2 web.qcow2 --script web.sh
```

A playbook runs from its own directory, against the VM as host `build`, reached as `debian`; a
script runs on it as root. Then [the base's seal](../images/mkosi.profiles/bake/mkosi.extra/usr/lib/homelab/seal) undoes
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
- **Check it keeps the base's contract**: `uv run homelab image test debian-13-amd64-vm --disk
  web.qcow2` boots it twice as new VMs, and checks what this page says.
- **Another kernel or layout** is an image of its own: a target, a profile in
  `images/mkosi.profiles/`; a file in `images/mkosi.conf.d/` matching it and a family, which names
  the image; and its entry in `images/images.toml`. `uv run homelab image build <image>` builds it
  over the bake its distribution and architecture share.

`derive` is one way; anything that boots the disk with a `cidata` drive works, as long as
`/usr/lib/homelab/seal` is the last thing it runs before powering off.
