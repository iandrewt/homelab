locals {
  # In this order: Proxmox puts netN in PCI slot 18+N, which udev names ens<18+N>
  vm_vnets = concat([local.node_vlan.vnet], try(local.cluster.vnets, []))
  vm_links = { for i, vnet in local.vm_vnets : vnet => "ens${18 + i}" }

  disk_storage = {
    for cell, c in local.proxmox : cell =>
    try(c.storages[c.guest_defaults.disk_storage].tenants, false) ? "${c.guest_defaults.disk_storage}-${local.team}" : c.guest_defaults.disk_storage
  }
}

# Proxmox CCM's ACL names this pool
resource "proxmox_virtual_environment_pool" "this" {
  for_each = toset([for n in local.cluster.nodes : n.proxmox])
  provider = proxmox.cluster[each.key]

  pool_id = "${local.team}/${local.name}"
  comment = "The ${local.name} Talos cluster."
}

module "vm" {
  source   = "../../modules/talos-vm"
  for_each = local.cluster.nodes
  providers = {
    proxmox = proxmox.cluster[each.value.proxmox]
  }

  node_name = each.value.host
  hostname  = each.key
  pool_id   = proxmox_virtual_environment_pool.this[each.value.proxmox].pool_id

  iso_file_id = "${local.proxmox[each.value.proxmox].image_storage}:iso/${yamldecode(file("${path.module}/../proxmox/images/${local.cluster.site}.yaml")).images["talos-metal-secureboot"].file_name}"

  cpu_cores         = local.cluster.classes[each.value.class].vm.cores
  memory_mb         = local.cluster.classes[each.value.class].vm.memory_mb
  disk_datastore_id = local.disk_storage[each.value.proxmox]
  disk_size_gb      = local.cluster.classes[each.value.class].vm.disk_gb

  vnets       = local.vm_vnets
  pci_mapping = try(local.cluster.classes[each.value.class].vm.pci_mapping, null)
}

# A device in the cluster is a node, its interfaces named as Talos names its links
data "netbox_cluster" "this" {
  name = local.name
}

data "netbox_devices" "metal" {
  filters = [{ name = "cluster_id", value = tostring(data.netbox_cluster.this.id) }]
}

data "netbox_device_interfaces" "metal" {
  for_each = { for d in data.netbox_devices.metal.devices : d.name => d.id }

  filters = [{ name = "device_id", value = tostring(each.value) }]
}

data "netbox_mac_addresses" "metal" {
  for_each = { for d in data.netbox_devices.metal.devices : d.name => d.id }

  filters = [{ name = "device_id", value = tostring(each.value) }]
}

locals {
  metal = {
    for name, found in data.netbox_device_interfaces.metal : name => {
      class   = one([for d in data.netbox_devices.metal.devices : d.custom_fields["class"] if d.name == name])
      secured = one([for d in data.netbox_devices.metal.devices : try(d.custom_fields["secure_boot"], "") == "true" && try(d.custom_fields["tpm"], "") == "true" if d.name == name])
      interfaces = {
        for vnet, v in local.subnets : vnet => one([
          for i in found.device_interfaces : {
            name        = i.name
            mac_address = one([for m in data.netbox_mac_addresses.metal[name].mac_addresses : m.mac_address if m.device_interface_id == i.id])
          }
          if i.untagged_vlan_id == (vnet == local.node_vlan.vnet ? local.node_vlan.id : local.vlan_by_vnet[vnet].id)
        ])
      }
    }
  }

  # EUI-64, from the MAC Proxmox or NetBox records rather than what the agent reports
  macs = merge(
    { for name, _ in local.cluster.nodes : name => { for i, vnet in local.vm_vnets : vnet => module.vm[name].macs[i] } },
    { for name, m in local.metal : name => { for vnet, i in m.interfaces : vnet => lower(i.mac_address) if i != null } },
  )

  eui64 = {
    for name, by_vnet in local.macs : name => {
      for vnet, mac in by_vnet : vnet => cidrhost(
        local.subnets[vnet].ipv6.cidr,
        parseint(format(
          "%02x%02x%02xfffe%02x%02x%02x",
          [for i, o in [for h in split(":", mac) : parseint(h, 16)] :
          i == 0 ? (floor(o / 2) % 2 == 1 ? o - 2 : o + 2) : o]...
        ), 16)
      )
    }
  }

  san = contains(try(local.cluster.vnets, []), "san") ? "san" : null

  nodes = merge(
    {
      for name, n in local.cluster.nodes : name => {
        class        = n.class
        address      = local.eui64[name][local.node_vlan.vnet]
        install_disk = local.cluster.classes[n.class].install_disk
        links        = { node = local.vm_links[local.node_vlan.vnet], san = local.san == null ? null : local.vm_links[local.san] }
        san_address  = local.san == null ? null : local.eui64[name][local.san]
      }
    },
    {
      for name, m in local.metal : name => {
        class        = m.class
        address      = local.eui64[name][local.node_vlan.vnet]
        install_disk = local.cluster.classes[m.class].install_disk
        links        = { node = m.interfaces[local.node_vlan.vnet].name, san = local.san == null ? null : m.interfaces[local.san].name }
        san_address  = local.san == null ? null : local.eui64[name][local.san]
      }
    },
  )
}

resource "terraform_data" "metal" {
  for_each = local.metal

  lifecycle {
    precondition {
      condition     = local.cluster.classes[each.value.class].lifecycle == "transient" || each.value.secured
      error_message = "${each.key} is in a permanent class, but NetBox doesn't record it with secure_boot and tpm: a permanent node seals its volumes to a TPM."
    }
  }
}

output "nodes" {
  value = { for name, n in local.nodes : name => { class = n.class, address = n.address } }
}
