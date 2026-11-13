resource "proxmox_virtual_environment_cluster_firewall" "this" {
  enabled = true

  input_policy  = "DROP"
  output_policy = "ACCEPT"

  # Every frame a VNet bridges passes forward, the gateway's included, so this waits for a
  # VNet firewall to admit them (bpg/terraform-provider-proxmox#3039 has its rules, not yet
  # its options)
  forward_policy = "ACCEPT"

  log_ratelimit {
    enabled = true
    rate    = "1/second"
    burst   = 5
  }
}

# Proxmox's own: what it holds reaches the GUI, SSH, VNC and SPICE through its built-in rules
resource "proxmox_virtual_environment_firewall_ipset" "management" {
  name    = "management"
  comment = "Allowed to reach the GUI, SSH and consoles. Managed by OpenTofu."

  dynamic "cidr" {
    for_each = concat(module.netbox.audiences["management"].ipv6, module.netbox.audiences["management"].ipv4)
    content {
      name = cidr.value
    }
  }
}

resource "proxmox_virtual_environment_firewall_ipset" "api" {
  name    = "api"
  comment = "Allowed to reach the API, not SSH. Managed by OpenTofu."

  dynamic "cidr" {
    for_each = module.netbox.trusted["proxmox-api"]
    content {
      name = cidr.value
    }
  }
}

# Proxmox admits cluster traffic from it, and guesses it from the node's own address unless told
resource "proxmox_virtual_environment_firewall_alias" "local_network" {
  name    = "local_network"
  cidr    = one(flatten([for v in module.netbox.vlans : v.subnets[*].cidr if v.cluster == terraform.workspace]))
  comment = "The corosync network. Managed by OpenTofu."
}

resource "proxmox_virtual_environment_firewall_rules" "cluster" {
  rule {
    comment = "API"
    type    = "in"
    action  = "ACCEPT"
    source  = "+dc/${proxmox_virtual_environment_firewall_ipset.api.name}"
    dport   = "8006"
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
