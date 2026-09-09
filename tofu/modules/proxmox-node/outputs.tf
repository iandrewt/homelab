output "zfs_pool_names" {
  description = "Names of the ZFS pools created on the node."
  value       = [for p in proxmox_node_disk_zfs.pools : p.name]
}

output "bond_interface_name" {
  description = "Bond interface name, or null with a single uplink."
  value       = one(proxmox_network_linux_bond.this[*].name)
}

output "bridge_interface_name" {
  description = "The node's VM traffic bridge."
  value       = proxmox_network_linux_bridge.this.name
}

output "management_interface_name" {
  description = "Management interface name, or null if not on the bridge."
  value = local.mgmt_dedicated ? one(proxmox_network_linux_bridge.management[*].name) : (
    local.mgmt_tagged ? one(proxmox_network_linux_vlan.management[*].name) : proxmox_network_linux_bridge.this.name
  )
}
