# Proxmox host storage: boot disk vs. data pool

Two independent things:

- **The boot pool** (`local`, plus whatever else the installer creates) comes from
  whatever the Proxmox ISO installer did on whatever disk(s) were selected at install
  time. That's a manual, physical step, out of scope for this repo, same as the install
  itself.
- **A separate data pool**, if the host has one, is raw disks beyond the boot disk.
  Creating it is the `proxmox_host` Ansible role's job (`tasks/zfs_data_pool.yml`, host
  configuration, no Proxmox API involved). Registering it as Proxmox storage is
  OpenTofu's job (`proxmox_storage_zfspool` in `tofu/envs/alfheim/main.tf`, a Proxmox-API
  resource, same lane as the VM and download-file resources defined alongside it).

Not every host has a separate data pool. `proxmox_host_data_pool_enabled` defaults to
`false`, which skips the pool-related tasks entirely - a single-disk node, or one where
the boot pool is also the only pool, needs no host_vars entry for this at all.

## Boot disk filesystem

The Proxmox installer's default filesystem is **ext4** (LVM-thin, storage IDs `local` +
`local-lvm`), not ZFS. The `local-zfs` storage ID only exists if ZFS was picked in the
installer's Options dialog at install time. Nothing in this repo assumes either choice.

A VM's `disk_datastore_id` (the `vm` OpenTofu module) has no default - it must always
be set explicitly, naming a real storage ID rather than assuming `local-zfs` exists. For
example, `hugin`'s disk points at a dedicated data pool
(`disk_datastore_id = proxmox_storage_zfspool.data.id`), not the boot pool - so the
disk's ZFS-specific tuning (`cache = none`, avoiding double-buffering on top of ZFS's own
ARC) is correct regardless of what filesystem the boot disk uses.

## Setting up a data pool

The pool structure is one host_vars value, set explicitly - there is no auto-discovery
that decides which disks to use.

```yaml
proxmox_host_data_pool_enabled: true
proxmox_host_data_pool_name: data
proxmox_host_data_pool_vdevs:
  - type: mirror
    disks:
      - /dev/disk/by-id/<disk-1>
      - /dev/disk/by-id/<disk-2>
  - type: mirror
    disks:
      - /dev/disk/by-id/<disk-3>
      - /dev/disk/by-id/<disk-4>
```

Always use `/dev/disk/by-id/*` paths, never `/dev/sdX` - device letters aren't stable
across reboots or a disk being re-seated, and naming the wrong disk claims its entire
contents.

To find the real `<disk-N>` values, run:

```
ansible-playbook discover-hardware.yml --limit <hostname>
```

This is read-only - it reports each disk's size, model, and `/dev/disk/by-id` paths,
flags which disk is the boot disk (don't use it here), and flags which candidates
already look used (existing partitions or a filesystem/pool signature). It makes no
changes itself.

Two mirror vdevs stripe across each other automatically - that's the RAID10 equivalent,
with no separate "striping" setting. Any real `zpool` vdev type works the same way
(`mirror`, `raidz`, `raidz1`, `raidz2`, `raidz3`, or omit `type` entirely for a plain
stripe). `zpool create` rejects an invalid type on its own with a clear error.

To apply:

```
ansible-playbook site.yml --limit <hostname> --tags zfs_data_pool
tofu apply
```

`tofu apply` must run after the Ansible step - `proxmox_storage_zfspool` requires the
pool to already exist. Then point a VM's `disk_datastore_id` at the pool.

## Growing a pool

Adding a vdev to an existing pool is a manual procedure, not automated:

1. `zpool status <pool-name>` to see the current layout.
2. `ansible-playbook discover-hardware.yml --limit <hostname>` to find the new disks'
   real identities - it flags which disks already look used, making it clear which are
   new.
3. `zpool add <pool-name> mirror /dev/disk/by-id/<new-1> /dev/disk/by-id/<new-2>` (or
   whatever vdev type the pool already uses - mixing vdev types within one pool is
   legal ZFS but not recommended).
4. Update `proxmox_host_data_pool_vdevs` in host_vars to match the pool's new real
   layout, so the repo continues to describe the pool as it actually is.

`zpool add` only ever adds what's named. There is no Ansible task that reads a vdev list
and reconciles it against a live pool, for either creation or growth: `community.
general.zpool`'s reconciling `state: present` mode does not work correctly against an
existing pool when using `/dev/disk/by-id` paths. It resolves the pool's current disks
to their underlying real device paths (e.g. `/dev/sdb`) internally, then compares those
against the raw by-id strings passed to it - the two never match, so it treats every
already-present disk as missing from the desired list and attempts to `zpool remove` it
before re-adding it. Use `zpool add` directly, not that module, against an existing
pool.

`tofu apply` does not need to run again after growing a pool - `proxmox_storage_zfspool`
references the pool by name, not by capacity.
