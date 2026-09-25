locals {
  # Naming a node through this map turns a typo into a validate-time "key not found"
  # rather than an API failure at apply.
  node = { for n in keys(local.cluster.nodes) : n => n }

  image = {
    for name, img in local.cluster.images :
    name => "${local.cluster.image_storage}:${img.content_type}/${img.file_name}"
  }
}

data "onepassword_item" "control_ssh_key" {
  vault = data.onepassword_vault.this.uuid
  title = "homelab control node SSH key"
}

module "hugin" {
  source = "../../../modules/linux-vm"

  node_name   = local.node["yggdrasil"]
  pool_id     = local.pool
  hostname    = "hugin"
  region      = local.cluster_name
  description = "DNS resolver."
  tags        = ["dns-resolver"]

  image_file_id = local.image["debian-13-genericcloud"]
  mac_prefix    = local.cluster.mac_prefix

  disk_datastore_id    = local.cluster.guest_defaults.disk_storage
  snippet_datastore_id = local.cluster.guest_defaults.snippet_storage

  cpu_cores    = 2
  memory_mb    = 1024
  disk_size_gb = 8

  vm_id   = 200
  vlan_id = module.netbox.vlans["Servers"]
  dhcp4   = true

  # Nothing a resolver needs to build itself may come from the VIP it will serve
  dns_servers = [for p in module.netbox.vlan_prefixes["Servers"] : cidrhost(p, 1)]

  ssh_public_keys = [data.onepassword_item.control_ssh_key.public_key]
}

output "vm_ids" {
  value = { hugin = module.hugin.vm_id }
}

output "macs" {
  value = { hugin = module.hugin.mac_address }
}
