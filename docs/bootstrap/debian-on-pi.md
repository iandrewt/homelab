# Bootstrapping a Debian-on-Raspberry-Pi host

Physical procedure, blank SD card to Ansible-reachable. Nothing here runs via Ansible or
OpenTofu - this is what has to happen before either tool can reach the box.

Replace `<hostname>` below with the value for the specific host.

## OS: Debian arm64, not Raspberry Pi OS

The `raspi` build under https://cloud.debian.org/images/cloud/ - a hybrid GPT/MBR
image (root as GPT partition 1, ESP/boot as GPT partition 15, hybrid-MBR'd so the Pi
3B's non-UEFI boot ROM can still find it) that works across the 3B/4/5 family. Same
cloud-init/NoCloud mechanism as every other VM in this project. Using plain Debian
instead of Raspberry Pi OS keeps every host in the fleet on the same OS family.

## 1. Flash

`prep-sd-card.sh` in this directory automates this - finds the latest image for a
codename, verifies its checksum, flashes it, and writes the seed, with a typed
confirmation before it touches the device:

```
./prep-sd-card.sh --device /dev/sdX --hostname <hostname> \
  --ssh-key ~/.ssh/id_ed25519.pub
```

Or source the key from 1Password instead of a local file:

```
./prep-sd-card.sh --device /dev/sdX --hostname <hostname> \
  --ssh-key-op "op://Vault/Item/public key"
```

Manually, the same steps:

1. Download the current image from `https://cloud.debian.org/images/cloud/<codename>/latest/`
   (look for `*-raspi-arm64.tar.xz`) and verify it against the `SHA512SUMS` in the same
   directory.
2. Extract `disk.raw` from the tarball and write it to the SD card (`dd`, Balena Etcher,
   or similar) - not the tarball itself.
3. Re-mount the card, find the partition at GPT type GUID
   `c12a7328-f81f-11d2-ba4b-00a0c93ec93b` (the ESP - not necessarily partition 1), and
   drop a NoCloud seed on it:

   ```yaml
   # meta-data
   instance-id: <hostname>
   local-hostname: <hostname>
   ```

   ```yaml
   # user-data
   #cloud-config
   hostname: <hostname>
   ssh_authorized_keys:
     - <control node's public key>
   ssh_pwauth: false
   timezone: Australia/Sydney
   ```

## 2. First boot

1. Insert the card, power it on. Wait ~90 seconds for cloud-init's first-boot run.
2. `ssh debian@<hostname>.local` - Debian's default install includes Avahi, so mDNS
   should work without knowing the DHCP-assigned address yet.

## 3. Give it its real address

DHCP reservations are manual across this fleet (no automatable UniFi API for it):

1. In the UniFi UI, find the new device's MAC and add a reservation.
2. Reboot, or wait for the lease to renew.

## 4. Hand off to Ansible

```
ansible-playbook site.yml --limit <hostname>
```

## 5. Verify

Whatever the assigned roles actually run - `systemctl status` the relevant services, confirm the host answers as expected for its role.
