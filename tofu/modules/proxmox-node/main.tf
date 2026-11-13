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
