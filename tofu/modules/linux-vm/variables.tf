variable "node_name" {
  type = string
}

variable "vm_id" {
  description = "Proxmox VMID, if the workload cares which. Unset, Proxmox takes the next free one."
  type        = number
  default     = null

  validation {
    condition     = var.vm_id == null || var.vm_id >= 100
    error_message = "Proxmox reserves VMIDs below 100."
  }
}

variable "hostname" {
  type = string
}

variable "pool_id" {
  description = "Proxmox pool the VM joins."
  type        = string
  default     = null
}

variable "image_file_id" {
  description = "The image's volume, e.g. from proxmox_download_file."
  type        = string
}

variable "description" {
  type    = string
  default = "Managed by OpenTofu (tofu/modules/linux-vm). Don't edit in the UI."
}

variable "cpu_cores" {
  description = "Number of CPU cores. Total number of vCPUs is cpu_cores * cpu_sockets."
  type        = number
  default     = 1
}

variable "cpu_sockets" {
  description = "Number of CPU sockets."
  type        = number
  default     = 1
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

variable "enable_backup" {
  type    = bool
  default = true
}

variable "pre_enrolled_keys" {
  description = "Whether to enroll the default Secure Boot keys into the EFI data."
  type        = bool
  default     = true
}

variable "vnet" {
  description = "The SDN VNet the NIC attaches to."
  type        = string
}

variable "ipv4" {
  description = "\"dhcp\", a static address in CIDR notation, or null for none."
  type        = string
  default     = "dhcp"
}

variable "ipv6" {
  description = "\"auto\" for SLAAC, \"dhcp\", a static address in CIDR notation, or null for none."
  type        = string
  default     = "auto"
}

variable "ipv4_gateway" {
  description = "Static IPv4 default route."
  type        = string
  default     = null
}

variable "ipv6_gateway" {
  description = "Static IPv6 default route."
  type        = string
  default     = null
}

variable "enable_multiqueue" {
  description = "Whether to enable multiqueue for the network interface. This can improve network performance for VMs with multiple CPU cores."
  type        = bool
  default     = true
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

variable "watchdog_action" {
  description = "Action to take when the watchdog timer expires."
  type        = string
  default     = "none"
}

variable "tags" {
  description = "Only on an existing VM for a pool-scoped token: Proxmox checks tags at create before the VM joins its pool."
  type        = list(string)
  default     = []
}
