module "netbox" {
  source = "../../modules/netbox-site"

  site_slug = local.site
}

# NetBox is the record of each: where it runs, its size and its VLAN. Hardware is `bootstrap pi`'s.
data "netbox_virtual_machines" "resolvers" {
  filter {
    name  = "site"
    value = local.site
  }

  filter {
    name  = "role"
    value = "dns-resolver"
  }
}

data "netbox_clusters" "all" {}

data "netbox_interfaces" "resolver" {
  for_each = { for vm in data.netbox_virtual_machines.resolvers.vms : vm.name => vm.vm_id }

  filter {
    name  = "vm_id"
    value = tostring(each.value)
  }
}

locals {
  clusters = { for c in data.netbox_clusters.all.clusters : c.cluster_id => c.name }
  by_vid   = { for name, v in module.netbox.vlans : v.vid => v }

  resolvers = {
    for vm in data.netbox_virtual_machines.resolvers.vms : vm.name => {
      proxmox   = local.clusters[vm.cluster_id]
      host      = vm.device_name
      cores     = vm.vcpus
      memory_mb = vm.memory_mb
      disk_gb   = ceil(vm.disk_size_mb / 1024)
      # The interface that's on a VLAN, besides lo and anycast0
      vlan = local.by_vid[one(flatten([for i in data.netbox_interfaces.resolver[vm.name].interfaces : i.untagged_vlan[*].vid]))]
    }
  }

}

data "onepassword_item" "library" {
  vault = data.onepassword_vault.team.uuid
  title = "Proxmox Image Library - ${local.site}"
}

locals {
  images = {
    for label, field in data.onepassword_item.library.section_map["images"].field_map : label => nonsensitive(field.value)
  }
}

data "onepassword_item" "ssh_key" {
  vault = data.onepassword_vault.team.uuid
  title = "SSH Key - ${module.tenancy.teams[local.team].title}"
}

resource "proxmox_virtual_environment_pool" "this" {
  for_each = toset([for r in local.resolvers : r.proxmox])
  provider = proxmox.cluster[each.key]

  pool_id = "${local.team}/dns"
  comment = "The resolvers."
}

module "resolver" {
  source   = "../../modules/linux-vm"
  for_each = local.resolvers
  providers = {
    proxmox = proxmox.cluster[each.value.proxmox]
  }

  node_name   = each.value.host
  pool_id     = proxmox_virtual_environment_pool.this[each.value.proxmox].pool_id
  hostname    = each.key
  description = "DNS resolver."

  image_file_id = local.images["ymir-amd64-vm"]
  # ymir enrolls its own Secure Boot keys
  pre_enrolled_keys = false

  disk_datastore_id = local.grants[each.value.proxmox]["disk storage"]

  cpu_cores    = each.value.cores
  memory_mb    = each.value.memory_mb
  disk_size_gb = each.value.disk_gb

  vnet = each.value.vlan.vnet

  # Nothing a resolver needs to build itself may come from the VIP it will serve
  dns_servers = compact(each.value.vlan.subnets[*].gateway)

  ssh_public_keys = [data.onepassword_item.ssh_key.public_key]
}

output "resolvers" {
  value = { for name, r in module.resolver : name => { vm_id = r.vm_id, mac = r.mac_address } }
}
