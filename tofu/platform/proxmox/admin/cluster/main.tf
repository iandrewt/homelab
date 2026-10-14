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
  source   = "../../../../modules/proxmox-node"
  for_each = local.cluster.nodes

  name  = each.key
  pools = each.value.pools
}

# --- SDN ---------------------------------------------------------------------------

locals {
  vnets = { for name, v in module.netbox.vlans : v.vnet => merge(v, { name = name }) if v.vnet != "" }

  subnets = merge([
    for vnet, v in local.vnets : { for s in v.subnets : "${vnet}|${s.cidr}" => merge(s, { vnet = vnet }) }
  ]...)
}

resource "proxmox_sdn_applier" "finalizer" {
  on_create = false
}

resource "proxmox_sdn_zone_vlan" "this" {
  id     = local.cluster.site
  bridge = local.cluster.sdn.bridge
  mtu    = local.cluster.sdn.mtu

  depends_on = [proxmox_sdn_applier.finalizer]
}

resource "proxmox_sdn_vnet" "this" {
  for_each = local.vnets

  id            = each.key
  zone          = proxmox_sdn_zone_vlan.this.id
  tag           = each.value.vid
  alias         = each.value.name
  isolate_ports = each.value.isolated

  depends_on = [proxmox_sdn_applier.finalizer]
}

resource "proxmox_sdn_subnet" "this" {
  for_each = local.subnets

  vnet    = proxmox_sdn_vnet.this[each.value.vnet].id
  cidr    = each.value.cidr
  gateway = each.value.gateway

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

# --- Backup ------------------------------------------------------------------------

resource "proxmox_backup_job" "this" {
  id      = "guests"
  comment = "Every guest. Managed by OpenTofu."

  all      = true
  storage  = proxmox_storage_nfs.this[local.cluster.backup.storage].id
  schedule = local.cluster.backup.schedule

  mode           = "snapshot"
  compress       = "zstd"
  notes_template = "{{guestname}}"
  prune_backups  = { for k, v in local.cluster.backup.retention : k => tostring(v) }
}
