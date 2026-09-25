variable "name" {
  description = "Node name."
  type        = string
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
