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

resource "proxmox_virtual_environment_firewall_ipset" "management" {
  name    = "management"
  comment = "Allowed to reach the GUI and SSH. Managed by OpenTofu."

  dynamic "cidr" {
    for_each = module.netbox.management_sources
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
