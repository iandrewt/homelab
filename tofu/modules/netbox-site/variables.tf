variable "site_slug" {
  type        = string
  description = "NetBox site slug. The natural key everything else is resolved from."
}

variable "management_vlans" {
  type        = list(string)
  default     = []
  description = "VLAN names whose prefixes may reach the Proxmox management interface."
}
