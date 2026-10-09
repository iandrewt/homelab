resource "proxmox_virtual_environment_cluster_firewall" "this" {
  enabled = true

  input_policy  = "DROP"
  output_policy = "ACCEPT"

  # Every frame a VNet bridges passes forward, the gateway's included. Guests are
  # filtered by their own firewalls, and VLANs from each other by the router.
  forward_policy = "ACCEPT"

  log_ratelimit {
    enabled = true
    rate    = "1/second"
    burst   = 5
  }
}

resource "proxmox_virtual_environment_firewall_ipset" "management" {
  name    = "management"
  comment = "Allowed to reach the GUI and SSH. Managed by OpenTofu."

  dynamic "cidr" {
    for_each = flatten([for name in try(local.cluster.firewall.management_vlans, []) : module.netbox.vlans[name].subnets[*].cidr])
    content {
      name = cidr.value
    }
  }
}

resource "proxmox_virtual_environment_firewall_rules" "cluster" {
  rule {
    comment = "GUI"
    type    = "in"
    action  = "ACCEPT"
    source  = "+dc/${proxmox_virtual_environment_firewall_ipset.management.name}"
    dport   = "8006"
    proto   = "tcp"
    log     = "nolog"
  }

  rule {
    comment = "SSH"
    type    = "in"
    action  = "ACCEPT"
    source  = "+dc/${proxmox_virtual_environment_firewall_ipset.management.name}"
    dport   = "22"
    proto   = "tcp"
    log     = "nolog"
  }

  # Without this a dropped INPUT policy also silently breaks ping and, on IPv6, the
  # neighbour discovery the management link depends on.
  rule {
    comment = "Ping"
    type    = "in"
    action  = "ACCEPT"
    source  = "+dc/${proxmox_virtual_environment_firewall_ipset.management.name}"
    macro   = "Ping"
    log     = "nolog"
  }
}
