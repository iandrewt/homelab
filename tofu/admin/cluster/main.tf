locals {
  pool_nodes = {
    for pool in toset(flatten([for _, n in local.cluster.nodes : keys(n.pools)])) :
    pool => sort([for name, n in local.cluster.nodes : name if contains(keys(n.pools), pool)])
  }
}

resource "proxmox_cluster_options" "this" {
  language = "en"
  keyboard = "en-us"
  console  = "xtermjs"

  mac_prefix = local.cluster.mac_prefix

  max_workers = 5

  # TODO: email_from, once there's somewhere for notifications to go.
}

module "node" {
  source   = "../../modules/proxmox-node"
  for_each = local.cluster.nodes

  name        = each.key
  bridge_name = local.cluster.bridge
  time_zone   = local.cluster.timezone

  vlan_aware = try(each.value.vlan_aware, true)

  bond_mode             = each.value.bond_mode
  bond_xmit_hash_policy = each.value.bond_xmit_hash_policy

  uplinks         = module.netbox.nodes[each.key].uplinks
  vids            = module.netbox.nodes[each.key].vids
  vlan_interfaces = module.netbox.nodes[each.key].vlan_interfaces

  management = {
    uplink       = try(each.value.management.uplink, null)
    vlan_id      = try(each.value.management.tagged, false) ? module.netbox.nodes[each.key].management.vlan_id : null
    ipv4_address = module.netbox.nodes[each.key].management.ipv4_address
    ipv4_gateway = module.netbox.nodes[each.key].management.ipv4_gateway
    ipv6_address = module.netbox.nodes[each.key].management.ipv6_address
    ipv6_gateway = module.netbox.nodes[each.key].management.ipv6_gateway
  }

  pools = each.value.pools
}

# --- Network -----------------------------------------------------------------------

import {
  for_each = local.cluster.nodes
  to       = module.node[each.key].proxmox_network_linux_bridge.this
  id       = "${each.key}:${local.cluster.bridge}"
}

# --- SDN ---------------------------------------------------------------------------

locals {
  vnets = {
    for vid in toset(flatten([for n, _ in local.cluster.nodes : module.netbox.nodes[n].vids])) :
    module.netbox.vnets[tostring(vid)].id => merge(module.netbox.vnets[tostring(vid)], { tag = vid })
  }

  subnets = merge([
    for vnet, v in local.vnets : { for p in v.prefixes : "${vnet}|${p}" => { vnet = vnet, cidr = p } }
  ]...)
}

resource "proxmox_sdn_applier" "finalizer" {
  on_create = false
}

resource "proxmox_sdn_zone_vlan" "this" {
  id     = local.cluster.site
  bridge = local.cluster.bridge

  depends_on = [module.node, proxmox_sdn_applier.finalizer]
}

resource "proxmox_sdn_vnet" "this" {
  for_each = local.vnets

  id            = each.key
  zone          = proxmox_sdn_zone_vlan.this.id
  tag           = each.value.tag
  alias         = each.value.name
  isolate_ports = each.value.isolated

  depends_on = [proxmox_sdn_applier.finalizer]
}

resource "proxmox_sdn_subnet" "this" {
  for_each = local.subnets

  vnet    = proxmox_sdn_vnet.this[each.value.vnet].id
  cidr    = each.value.cidr
  gateway = cidrhost(each.value.cidr, 1)

  depends_on = [proxmox_sdn_applier.finalizer]
}

resource "proxmox_sdn_applier" "this" {
  lifecycle {
    replace_triggered_by = [proxmox_sdn_zone_vlan.this, proxmox_sdn_vnet.this, proxmox_sdn_subnet.this]
  }

  depends_on = [proxmox_sdn_zone_vlan.this, proxmox_sdn_vnet.this, proxmox_sdn_subnet.this]
}

# --- Storage -----------------------------------------------------------------------

# The installer always names its directory storage `local`
import {
  to = proxmox_storage_directory.this["local"]
  id = "local"
}

resource "proxmox_storage_directory" "this" {
  for_each = { for id, s in local.cluster.storages : id => s if s.type == "directory" }

  id      = each.key
  path    = each.value.path
  content = each.value.content
  shared  = false
}

resource "proxmox_storage_nfs" "this" {
  for_each = { for id, s in local.cluster.storages : id => s if s.type == "nfs" }

  id      = each.key
  server  = each.value.server
  export  = each.value.export
  content = each.value.content
  options = try(each.value.options, null)
  nodes   = sort(keys(local.cluster.nodes))

  create_subdirs = true
}

resource "proxmox_storage_zfspool" "this" {
  for_each = { for id, s in local.cluster.storages : id => s if s.type == "zfspool" }

  id       = each.key
  zfs_pool = each.value.pool
  nodes    = local.pool_nodes[each.value.pool]

  content        = each.value.content
  thin_provision = try(each.value.thin_provision, true)

  depends_on = [module.node]
}
