variable "name" {
  description = "Node name."
  type        = string
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
