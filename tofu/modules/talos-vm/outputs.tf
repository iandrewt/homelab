output "vm_id" {
  value = proxmox_virtual_environment_vm.this.vm_id
}

output "macs" {
  description = "One per VNet, in order, as Proxmox generated them."
  value       = [for d in proxmox_virtual_environment_vm.this.network_device : lower(d.mac_address)]
}
