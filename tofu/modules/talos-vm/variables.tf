variable "node_name" {
  type = string
}

variable "hostname" {
  type = string
}

variable "pool_id" {
  type    = string
  default = null
}

variable "description" {
  type    = string
  default = "Managed by OpenTofu (tofu/modules/talos-vm). Don't edit in the UI."
}

variable "tags" {
  type    = list(string)
  default = []
}

variable "iso_file_id" {
  description = "The Talos boot ISO. A blank disk falls through to it; an installed one boots itself."
  type        = string
}

variable "cpu_cores" {
  type = number
}

variable "memory_mb" {
  type = number
}

variable "disk_datastore_id" {
  type = string
}

variable "disk_size_gb" {
  type = number
}

variable "vnets" {
  description = "SDN VNets, one NIC each, in order."
  type        = list(string)
}

variable "pci_mapping" {
  description = "A PCI mapping to pass through, by name."
  type        = string
  default     = null
}

variable "agent_timeout" {
  description = "How long to wait for the guest agent - the node's first boot, Secure Boot enrollment included."
  type        = string
  default     = "15m"
}

variable "started" {
  type    = bool
  default = true
}

variable "on_boot" {
  type    = bool
  default = true
}
