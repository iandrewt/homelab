variable "site_slug" {
  type        = string
  description = "NetBox site slug. The natural key everything else is resolved from."
}

variable "nodes" {
  type        = list(string)
  default     = []
  description = "Cluster node names, each of which must have a named address in NetBox."
}

variable "bridge" {
  type        = string
  default     = "vmbr0"
  description = "Each node's VM-traffic bridge in NetBox: its tagged VLANs are the trunk, its sub-interfaces the node's own L2."
}

variable "bond" {
  type        = string
  default     = "bond0"
  description = "Each node's LAG in NetBox. Its members are the uplinks."
}

variable "management_vlans" {
  type        = list(string)
  default     = []
  description = "VLAN names whose prefixes may reach the Proxmox management interface."
}
