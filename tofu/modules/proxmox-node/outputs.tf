output "zfs_pool_names" {
  description = "Names of the ZFS pools created on the node."
  value       = [for p in proxmox_node_disk_zfs.pools : p.name]
}
