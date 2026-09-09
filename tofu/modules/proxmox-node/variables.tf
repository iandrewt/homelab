variable "name" {
  description = "Node name."
  type        = string
}

variable "uplinks" {
  description = "NICs carrying VM traffic, bonded together if more than one."
  type        = list(string)

  validation {
    condition     = length(var.uplinks) > 0
    error_message = "At least one uplink must be provided."
  }
}

variable "bridge_name" {
  type    = string
  default = "vmbr0"
}

variable "vlan_aware" {
  description = "False for a flat network with no VLANs available; management then has to sit untagged on the bridge."
  type        = bool
  default     = true
}

variable "vids" {
  description = "VLAN IDs the bridge will pass. Ignored when vlan_aware is false."
  type        = string
  default     = "2-4094"
}

variable "bond_mode" {
  description = "Bonding mode. Ignored with a single uplink."
  type        = string
  default     = "802.3ad"

  validation {
    condition     = length(var.uplinks) > 1 || var.bond_mode == "802.3ad"
    error_message = "bond_mode only applies with more than one uplink."
  }
}

variable "bond_xmit_hash_policy" {
  description = "Transmit hash policy. Ignored with a single uplink."
  type        = string
  default     = "layer3+4"

  validation {
    condition     = length(var.uplinks) > 1 || var.bond_xmit_hash_policy == "layer3+4"
    error_message = "bond_xmit_hash_policy only applies with more than one uplink."
  }
}

variable "management_bridge_name" {
  description = "Bridge carrying management on a node with a NIC to spare."
  type        = string
  default     = "mgmt"
}

variable "management" {
  description = "Where the management address lives and what it is."
  type = object({
    uplink       = optional(string)
    vlan_id      = optional(number)
    ipv4_address = optional(string)
    ipv4_gateway = optional(string)
    ipv6_address = optional(string)
    ipv6_gateway = optional(string)
  })

  validation {
    condition     = var.management.uplink == null || var.management.vlan_id == null
    error_message = "management.uplink and management.vlan_id are mutually exclusive."
  }

  validation {
    condition     = !contains(var.uplinks, coalesce(var.management.uplink, "\u0000"))
    error_message = "management.uplink is also listed in uplinks - a NIC can't carry VM traffic and be given over to management."
  }

  validation {
    condition     = var.management.vlan_id == null || var.vlan_aware
    error_message = "A tagged management subinterface needs a VLAN-aware bridge."
  }

  validation {
    condition = (
      (var.management.ipv4_address != null && var.management.ipv4_gateway != null) ||
      (var.management.ipv6_address != null && var.management.ipv6_gateway != null)
    )
    error_message = "management needs at least one fully configured address family - an address together with its gateway. Single-stack IPv4 or single-stack IPv6 both satisfy this."
  }

  validation {
    condition     = var.management.ipv4_gateway == null || var.management.ipv4_address != null
    error_message = "management.ipv4_gateway is set with no ipv4_address to reach it from."
  }

  validation {
    condition     = var.management.ipv6_gateway == null || var.management.ipv6_address != null
    error_message = "management.ipv6_gateway is set with no ipv6_address to reach it from."
  }

  validation {
    condition     = var.management.ipv4_address == null || can(cidrnetmask(var.management.ipv4_address))
    error_message = "management.ipv4_address must be CIDR notation."
  }
}

variable "pools" {
  description = "ZFS pools to create, keyed by pool name."
  type = map(object({
    devices     = list(string)
    raidlevel   = string
    ashift      = optional(number, 12)
    compression = optional(string, "on")
  }))
  default = {}

  validation {
    condition     = alltrue([for p in var.pools : length(p.devices) > 0])
    error_message = "Every pool needs at least one device."
  }

  validation {
    condition     = alltrue([for p in var.pools : alltrue([for d in p.devices : startswith(d, "/dev/disk/by-id/")])])
    error_message = "Devices must be /dev/disk/by-id/* paths - /dev/sdX isn't stable across reboots."
  }
}

variable "firewall_configuration" {
  description = "Node firewall configuration."
  type = object({
    log_level_in                         = optional(string, "nolog")
    log_level_out                        = optional(string, "nolog")
    log_level_forward                    = optional(string, "nolog")
    ndp                                  = optional(bool, true)
    nftables                             = optional(bool, true)
    nosmurfs                             = optional(bool, true)
    smurf_log_level                      = optional(string, "nolog")
    tcp_flags_log_level                  = optional(string, "nolog")
    nf_conntrack_max                     = optional(number, 262144)
    nf_conntrack_tcp_timeout_established = optional(number, 432000)
  })
  default = {}
}

variable "time_zone" {
  description = "tzdata name, e.g. Australia/Sydney."
  type        = string
}
