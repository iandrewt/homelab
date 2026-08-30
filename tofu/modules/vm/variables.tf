variable "node_name" {
  type = string
}

variable "vm_id" {
  description = "Proxmox VMID. No auto-allocation - pick deliberately so it's stable in Git."
  type        = number
}

variable "hostname" {
  type = string
}

variable "image_file_id" {
  description = "Already-imported cloud image, e.g. from proxmox_download_file."
  type        = string
}

variable "description" {
  type    = string
  default = "Managed by OpenTofu (tofu/modules/vm) + Ansible. Don't edit in the UI."
}

variable "cpu_cores" {
  type    = number
  default = 1
}

variable "memory_mb" {
  type = number
}

variable "disk_datastore_id" {
  type        = string
}

variable "disk_size_gb" {
  type = number
}

variable "bridge" {
  type    = string
  default = "vmbr0"
}

variable "vlan_id" {
  description = "null means untagged."
  type        = number
  default     = null
}

variable "ipv4_cidr" {
  description = "e.g. \"10.176.20.11/24\", or \"dhcp\"."
  type        = string
}

variable "ipv4_gateway" {
  description = "Required unless ipv4_cidr is \"dhcp\"."
  type        = string
  default     = null
}

variable "ipv6_cidr" {
  description = "e.g. \"fd1b:134f:fb00:20::11/64\", \"auto\" for SLAAC, or \"dhcp\" for DHCPv6."
  type        = string
}

variable "ipv6_gateway" {
  description = "Required unless ipv6_cidr is \"auto\" or \"dhcp\"."
  type        = string
  default     = null
}

variable "ci_username" {
  description = "The image's own default user."
  type        = string
}

variable "ssh_public_keys" {
  type = list(string)
}

variable "started" {
  type    = bool
  default = true
}

variable "on_boot" {
  description = "Start this VM automatically when the Proxmox host boots."
  type        = bool
  default     = true
}

variable "tags" {
  type    = list(string)
  default = []
}
