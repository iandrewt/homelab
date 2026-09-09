locals {
  # Identity map over the cluster's own nodes. Going through it to name a node means a
  # typo is a "key not found" error at validate, rather than a confusing API failure at
  # apply - which is what the cluster file's VM list used to catch in CI.
  node = { for n in keys(local.cluster.nodes) : n => n }

  image = {
    for name, img in local.cluster.images :
    name => "${local.cluster.image_storage}:${img.content_type}/${img.file_name}"
  }
}

data "onepassword_item" "control_ssh_key" {
  vault = local.cluster.onepassword_vault
  title = "homelab control node SSH key"
}

module "hugin" {
  source = "../../../modules/linux-vm"

  node_name   = local.node["yggdrasil"]
  pool_id     = local.pool
  vm_id       = 200
  hostname    = "hugin"
  description = "DNS resolver (primary)."
  tags        = ["dns-resolver"]

  image_file_id = local.image["debian-13-genericcloud"]
  mac_prefix    = local.cluster.mac_prefix

  disk_datastore_id    = local.cluster.guest_defaults.disk_storage
  snippet_datastore_id = local.cluster.guest_defaults.snippet_storage

  cpu_cores    = 2
  memory_mb    = 1024
  disk_size_gb = 8

  vlan_id = local.ipam.hosts["hugin"].vlan_id

  enable_dhcpv4 = true
  enable_dhcpv6 = true

  static_ip_addresses = [
    local.ipam.hosts["hugin"].ipv6_address,
  ]

  dns_servers    = local.ipam.hosts["hugin"].dns_servers
  search_domains = local.ipam.hosts["hugin"].search_domains

  ssh_public_keys = [data.onepassword_item.control_ssh_key.public_key]
}

output "vm_ids" {
  value = { hugin = module.hugin.vm_id }
}
