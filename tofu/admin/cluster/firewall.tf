resource "proxmox_virtual_environment_cluster_firewall" "this" {
  enabled = true

  input_policy  = "DROP"
  output_policy = "ACCEPT"

  # Traffic between VMs on the same bridge in the same VLAN
  forward_policy = "DROP"

  log_ratelimit {
    enabled = true
    rate    = "1/second"
    burst   = 5
  }
}

resource "proxmox_virtual_environment_firewall_alias" "management" {
  for_each = {
    for cidr in local.cluster.firewall.management_sources :
    "mgmt-${replace(replace(replace(cidr, ".", "-"), ":", ""), "/", "-")}" => cidr
  }

  name    = each.key
  cidr    = each.value
  comment = "Allowed to reach the GUI and SSH. Managed by OpenTofu."
}

resource "proxmox_virtual_environment_firewall_rules" "cluster" {
  dynamic "rule" {
    for_each = proxmox_virtual_environment_firewall_alias.management
    content {
      comment = "GUI from ${rule.value.cidr}"
      type    = "in"
      action  = "ACCEPT"
      source  = rule.value.name
      dport   = "8006"
      proto   = "tcp"
      log     = "nolog"
    }
  }

  dynamic "rule" {
    for_each = proxmox_virtual_environment_firewall_alias.management
    content {
      comment = "SSH from ${rule.value.cidr}"
      type    = "in"
      action  = "ACCEPT"
      source  = rule.value.name
      dport   = "22"
      proto   = "tcp"
      log     = "nolog"
    }
  }

  # Without this a dropped INPUT policy also silently breaks ping and, on IPv6, the
  # neighbour discovery the management link depends on.
  dynamic "rule" {
    for_each = proxmox_virtual_environment_firewall_alias.management
    content {
      comment = "Ping from ${rule.value.cidr}"
      type    = "in"
      action  = "ACCEPT"
      source  = rule.value.name
      macro   = "Ping"
      log     = "nolog"
    }
  }
}
