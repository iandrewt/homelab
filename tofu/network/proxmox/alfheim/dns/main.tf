locals {
  # Naming a node through this map turns a typo into a validate-time "key not found"
  # rather than an API failure at apply.
  node = { for n in keys(local.cluster.nodes) : n => n }

  vnet = "servers"

  image = {
    for name, img in local.cluster.images :
    name => "${local.cluster.image_storage}:${img.content_type}/${img.file_name}"
  }
}

resource "proxmox_virtual_environment_pool" "this" {
  pool_id = "${local.tenant}/dns"
  comment = "The resolvers."
}

data "onepassword_item" "control_ssh_key" {
  vault = data.onepassword_vault.this.uuid
  title = "homelab control node SSH key"
}

module "hugin" {
  source = "../../../../modules/linux-vm"

  node_name   = local.node["yggdrasil"]
  pool_id     = proxmox_virtual_environment_pool.this.pool_id
  hostname    = "hugin"
  region      = local.cluster_name
  description = "DNS resolver."

  image_file_id = local.image["debian-13-genericcloud"]

  disk_datastore_id    = local.cluster.guest_defaults.disk_storage
  snippet_datastore_id = local.cluster.guest_defaults.snippet_storage

  cpu_cores    = 2
  memory_mb    = 1024
  disk_size_gb = 8

  vm_id = 200
  vnet  = local.vnet

  # Nothing a resolver needs to build itself may come from the VIP it will serve
  dns_servers = one([for v in module.netbox.vlans : v.subnets[*].gateway if v.vnet == local.vnet])

  ssh_public_keys = [data.onepassword_item.control_ssh_key.public_key]
}

output "vm_ids" {
  value = { hugin = module.hugin.vm_id }
}

output "macs" {
  value = { hugin = module.hugin.mac_address }
}
