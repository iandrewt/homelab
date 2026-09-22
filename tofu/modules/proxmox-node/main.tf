locals {
  apt_repos = {
    "no-subscription"       = true
    "enterprise"            = false
    "ceph-squid-enterprise" = false
  }

  bonded = length(var.uplinks) > 1

  mgmt_dedicated = var.management.uplink != null
  mgmt_tagged    = !local.mgmt_dedicated && var.management.vlan_id != null
  mgmt_untagged  = !local.mgmt_dedicated && var.management.vlan_id == null
}

# Destroy runs in reverse dependency order, so this one - created first, destroyed last -
# is what activates the staged deletions. Nothing is staged yet on create.
resource "proxmox_network_applier" "finalizer" {
  node_name = var.name
  on_create = false
}

resource "proxmox_network_linux_bond" "this" {
  count = local.bonded ? 1 : 0

  node_name             = var.name
  name                  = "bond0"
  slaves                = var.uplinks
  bond_mode             = var.bond_mode
  bond_xmit_hash_policy = var.bond_xmit_hash_policy

  reload     = false
  depends_on = [proxmox_network_applier.finalizer]
}

resource "proxmox_network_linux_bridge" "this" {
  node_name  = var.name
  name       = var.bridge_name
  ports      = [coalesce(one(proxmox_network_linux_bond.this[*].name), var.uplinks[0])]
  vlan_aware = var.vlan_aware
  vids       = var.vlan_aware && length(var.vids) > 0 ? join(" ", [for v in var.vids : tostring(v)]) : null
  comment    = "VM traffic. Managed by OpenTofu."

  address  = local.mgmt_untagged ? var.management.ipv4_address : null
  gateway  = local.mgmt_untagged ? var.management.ipv4_gateway : null
  address6 = local.mgmt_untagged ? var.management.ipv6_address : null
  gateway6 = local.mgmt_untagged ? var.management.ipv6_gateway : null

  reload     = false
  depends_on = [proxmox_network_applier.finalizer]
}

resource "proxmox_network_linux_bridge" "management" {
  count = local.mgmt_dedicated ? 1 : 0

  node_name  = var.name
  name       = var.management_bridge_name
  ports      = [var.management.uplink]
  vlan_aware = false
  comment    = "Management. Managed by OpenTofu."

  address  = var.management.ipv4_address
  gateway  = var.management.ipv4_gateway
  address6 = var.management.ipv6_address
  gateway6 = var.management.ipv6_gateway

  reload     = false
  depends_on = [proxmox_network_linux_bridge.this]
}

# Tagged management, for a node with no spare NIC whose switch port is a pure trunk.
resource "proxmox_network_linux_vlan" "management" {
  count = local.mgmt_tagged ? 1 : 0

  node_name = var.name
  name      = "${var.bridge_name}.${var.management.vlan_id}"
  interface = proxmox_network_linux_bridge.this.name
  vlan      = var.management.vlan_id
  autostart = true
  comment   = "Management. Managed by OpenTofu."

  address  = var.management.ipv4_address
  gateway  = var.management.ipv4_gateway
  address6 = var.management.ipv6_address
  gateway6 = var.management.ipv6_gateway

  reload     = false
  depends_on = [proxmox_network_applier.finalizer]
}

resource "proxmox_network_linux_vlan" "this" {
  for_each = var.vlan_interfaces

  node_name = var.name
  name      = "${var.bridge_name}.${each.key}"
  interface = proxmox_network_linux_bridge.this.name
  vlan      = tonumber(each.key)
  autostart = true
  comment   = coalesce(each.value.comment, "Managed by OpenTofu.")

  address  = each.value.ipv4_address
  address6 = each.value.ipv6_address

  reload     = false
  depends_on = [proxmox_network_applier.finalizer]
}

resource "proxmox_network_applier" "this" {
  node_name = var.name
  # on_pending = true
  on_destroy = false

  depends_on = [
    proxmox_network_linux_bond.this,
    proxmox_network_linux_bridge.this,
    proxmox_network_linux_bridge.management,
    proxmox_network_linux_vlan.management,
    proxmox_network_linux_vlan.this,
  ]
}

resource "proxmox_node_firewall" "this" {
  node_name                            = var.name
  enabled                              = true
  log_level_in                         = var.firewall_configuration.log_level_in
  log_level_out                        = var.firewall_configuration.log_level_out
  log_level_forward                    = var.firewall_configuration.log_level_forward
  ndp                                  = var.firewall_configuration.ndp
  nftables                             = var.firewall_configuration.nftables
  nosmurfs                             = var.firewall_configuration.nosmurfs
  smurf_log_level                      = var.firewall_configuration.smurf_log_level
  tcp_flags_log_level                  = var.firewall_configuration.tcp_flags_log_level
  nf_conntrack_max                     = var.firewall_configuration.nf_conntrack_max
  nf_conntrack_tcp_timeout_established = var.firewall_configuration.nf_conntrack_tcp_timeout_established
}

resource "proxmox_apt_standard_repository" "repos" {
  for_each = local.apt_repos

  handle = each.key
  node   = var.name
}

resource "proxmox_apt_repository" "repos" {
  for_each = local.apt_repos

  enabled   = each.value
  file_path = proxmox_apt_standard_repository.repos[each.key].file_path
  index     = proxmox_apt_standard_repository.repos[each.key].index
  node      = proxmox_apt_standard_repository.repos[each.key].node
}

resource "proxmox_node_disk_zfs" "pools" {
  for_each = var.pools

  node_name = var.name
  name      = each.key
  devices   = each.value.devices
  raidlevel = each.value.raidlevel

  ashift      = each.value.ashift
  compression = each.value.compression

  add_storage    = false
  cleanup_config = true
  cleanup_disks  = false
}

resource "proxmox_virtual_environment_time" "this" {
  node_name = var.name
  time_zone = var.time_zone
}
