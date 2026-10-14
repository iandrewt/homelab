locals {
  # In this order: Proxmox puts netN in PCI slot 18+N, which udev names ens<18+N>
  vnets = ["k8s", "storage"]
  links = { for i, vnet in local.vnets : vnet => "ens${18 + i}" }

  classes = {
    controlplane = {
      machine_type = "controlplane"
      cores        = 4
      memory_mb    = 10240
      disk_gb      = 32
      extensions   = ["siderolabs/qemu-guest-agent"]
      talos        = "v1.14.2"
    }
    gpu = {
      machine_type = "worker"
      cores        = 4
      memory_mb    = 12288
      disk_gb      = 64
      extensions   = ["siderolabs/i915", "siderolabs/qemu-guest-agent"]
      talos        = "v1.14.2"
      pci_mapping  = "arc-a40"
      labels       = { "node.homelab.io/gpu" = "arc" }
    }
  }

  nodes = {
    talos-cp-01         = { class = "controlplane", node = "yggdrasil" }
    talos-cp-02         = { class = "controlplane", node = "yggdrasil" }
    talos-cp-03         = { class = "controlplane", node = "yggdrasil" }
    talos-worker-gpu-01 = { class = "gpu", node = "yggdrasil" }
  }

  # The VLAN each NIC joins, by family
  subnets = {
    for vnet in local.vnets : vnet => {
      for s in one([for v in module.netbox.vlans : v if v.vnet == vnet]).subnets :
      (strcontains(s.cidr, ":") ? "ipv6" : "ipv4") => s
    }
  }
}

resource "proxmox_virtual_environment_pool" "this" {
  pool_id = "${local.tenant}/valhalla"
  comment = "The valhalla Talos cluster."
}

module "node" {
  source   = "../../../../modules/talos-vm"
  for_each = local.nodes

  node_name = each.value.node
  hostname  = each.key
  pool_id   = proxmox_virtual_environment_pool.this.pool_id

  iso_file_id = "${local.cluster.image_storage}:iso/${local.cluster.images["talos-metal-secureboot"].file_name}"

  cpu_cores         = local.classes[each.value.class].cores
  memory_mb         = local.classes[each.value.class].memory_mb
  disk_datastore_id = local.cluster.guest_defaults.disk_storage
  disk_size_gb      = local.classes[each.value.class].disk_gb

  vnets       = local.vnets
  pci_mapping = try(local.classes[each.value.class].pci_mapping, null)
}

locals {
  # Talos leaves addr_gen_mode at the kernel's default, so a node's SLAAC address is EUI-64
  # from the MAC Proxmox gave it - in the VM's config, so never lost with the agent.
  address = {
    for name, _ in local.nodes : name => cidrhost(
      local.subnets.k8s.ipv6.cidr,
      parseint(format(
        "%02x%02x%02xfffe%02x%02x%02x",
        [for i, o in [for h in split(":", module.node[name].macs[0]) : parseint(h, 16)] :
        i == 0 ? (floor(o / 2) % 2 == 1 ? o - 2 : o + 2) : o]...
      ), 16)
    )
  }

  controlplane = [for name, n in local.nodes : local.address[name] if local.classes[n.class].machine_type == "controlplane"]
}

output "nodes" {
  value = { for name, n in local.nodes : name => { vm_id = module.node[name].vm_id, address = local.address[name] } }
}
