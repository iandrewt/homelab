resource "proxmox_cluster_options" "this" {
  language = "en"
  keyboard = "en-us"
  console  = "xtermjs"

  mac_prefix = local.cluster.mac_prefix

  max_workers = 5

  # TODO: email_from, once there's somewhere for notifications to go.

  # A guest is reached at its MAC's EUI-64, and cells share VNets, so a duplicate prefix is a collision waiting
  lifecycle {
    precondition {
      condition = length([
        for f in fileset("${path.module}/../../clusters", "*.yaml") : f
        if yamldecode(file("${path.module}/../../clusters/${f}")).mac_prefix == local.cluster.mac_prefix
      ]) == 1
      error_message = "Another cell's file has mac_prefix ${local.cluster.mac_prefix}: give this one a nibble of its own."
    }
  }
}

module "node" {
  source   = "../../../../modules/proxmox-node"
  for_each = local.cluster.nodes

  name = each.key
}

# --- SDN ---------------------------------------------------------------------------

locals {
  # The VLAN-aware bridge every node trunks its VNets on, as ifupdown2 builds it from NetBox
  trunks = distinct(flatten([
    for node, d in data.netbox_device_interfaces.nodes : [
      for i in d.device_interfaces : { name = i.name, mtu = i.mtu } if i.type == "bridge" && i.mode == "tagged"
    ]
  ]))
  trunk = one(local.trunks)

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
  bridge = local.trunk.name
  mtu    = local.trunk.mtu

  depends_on = [proxmox_sdn_applier.finalizer]

  # A VNet's bridge sits on the zone's on every node, so they all have to be the same one
  lifecycle {
    precondition {
      condition     = length(local.trunks) == 1 && length(data.netbox_device_interfaces.nodes) == length(local.cluster.nodes)
      error_message = "Every node in ${terraform.workspace} needs one tagged bridge in NetBox, with the same name and MTU: ${jsonencode(local.trunks)}."
    }
  }
}

resource "proxmox_sdn_vnet" "this" {
  for_each = local.vnets

  id            = each.key
  zone          = proxmox_sdn_zone_vlan.this.id
  tag           = each.value.vid
  alias         = each.value.name
  isolate_ports = contains(each.value.tags, "isolate-ports")

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

locals {
  # A storage marked `tenants` is one per tenant, on the tenant's own dataset
  storages = { for split in flatten([
    for id, s in local.cluster.storages : [
      for tenant in try(s.tenants, false) ? keys(local.cluster.tenants) : [""] : {
        id     = tenant == "" ? id : "${id}-${tenant}"
        s      = s
        suffix = tenant == "" ? "" : "/${tenant}"
      }
    ]
  ]) : split.id => split }
}

resource "proxmox_storage_nfs" "this" {
  for_each = { for id, split in local.storages : id => split if split.s.type == "nfs" }

  id      = each.key
  server  = each.value.s.server
  export  = "${each.value.s.export}${each.value.suffix}"
  content = each.value.s.content
  options = try(each.value.s.options, null)
  nodes   = sort(keys(local.cluster.nodes))

  create_subdirs = true
}

resource "proxmox_storage_zfspool" "this" {
  for_each = { for id, split in local.storages : id => split if split.s.type == "zfspool" }

  id       = each.key
  zfs_pool = "${each.value.s.pool}${each.value.suffix}"
  nodes    = sort(try(each.value.s.nodes, keys(local.cluster.nodes)))

  content        = each.value.s.content
  thin_provision = try(each.value.s.thin_provision, true)
}

# --- Mappings ----------------------------------------------------------------------

resource "proxmox_hardware_mapping_pci" "this" {
  for_each = try(local.cluster.mappings.pci, {})

  name    = each.key
  comment = try(each.value.comment, null)

  map = [
    for node, d in each.value.devices : {
      node         = node
      path         = d.path
      id           = d.id
      subsystem_id = d.subsystem_id
      iommu_group  = d.iommu_group
    }
  ]
}

# --- Backup ------------------------------------------------------------------------

data "proxmox_virtual_environment_pools" "all" {}

locals {
  # vzdump takes a pool's own members, never its sub-pools', and a tenant makes its workloads as
  # pools under its own: so a job per pool, and a pool a tenant makes waits for the next apply
  backed_up = {
    for pool in data.proxmox_virtual_environment_pools.all.pool_ids : pool => split("/", pool)[0]
    if can(local.cluster.tenants[split("/", pool)[0]].backup)
  }
}

resource "proxmox_backup_job" "this" {
  for_each = local.backed_up

  id      = "pool-${replace(each.key, "/", "-")}"
  comment = "The ${each.key} pool's guests. Managed by OpenTofu."

  pool     = each.key
  storage  = proxmox_storage_nfs.this["${local.cluster.backup.storage}-${each.value}"].id
  schedule = local.cluster.backup.schedule

  mode           = "snapshot"
  compress       = "zstd"
  notes_template = "{{guestname}}"
  prune_backups  = { for k, v in local.cluster.tenants[each.value].backup.retention : k => tostring(v) }
}
