variable "node_name" {
  type = string
}

variable "vm_id" {
  description = "Proxmox VMID. Must be unique in the cluster."
  type        = number
}

variable "hostname" {
  type = string
}

variable "mac_prefix" {
  description = "Three-octet OUI the VM's MAC is derived under. Must match the cluster's mac_prefix."
  type        = string

  validation {
    condition     = can(regex("^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){2}$", var.mac_prefix))
    error_message = "mac_prefix must be three colon-separated hex octets, e.g. BC:24:11."
  }
}

variable "pool_id" {
  description = "Proxmox pool the VM joins."
  type        = string
  default     = null
}

variable "image_file_id" {
  description = "Already-imported cloud image, e.g. from proxmox_download_file."
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

variable "snippet_datastore_id" {
  description = "Datastore for the four cloud-init snippets. Must be node-local."
  type        = string
  default     = "local"
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

variable "bridge" {
  type    = string
  default = "vmbr0"
}

variable "vlan_id" {
  description = "null means untagged."
  type        = number
  default     = null
}

variable "enable_dhcpv4" {
  description = "Whether to enable DHCPv4 on the VM's network interface."
  type        = bool
  default     = true
}

variable "enable_dhcpv6" {
  description = "Whether to enable DHCPv6 on the VM's network interface."
  type        = bool
  default     = true
}

variable "static_ip_addresses" {
  description = "List of static IP addresses to assign to the VM's network interface. Must be in CIDR notation."
  type        = list(string)
  default     = []
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

variable "accept_ra" {
  description = "Accept IPv6 router advertisements."
  type        = bool
  default     = true
}

variable "dns_servers" {
  description = "List of DNS servers to configure on the VM."
  type        = list(string)
  default     = []
}

variable "search_domains" {
  description = "List of search domains to configure on the VM."
  type        = list(string)
  default     = []
}

variable "enable_firewall" {
  description = "Whether to enable the Proxmox firewall for this VM."
  type        = bool
  default     = true
}

variable "enable_multiqueue" {
  description = "Whether to enable multiqueue for the network interface. This can improve network performance for VMs with multiple CPU cores."
  type        = bool
  default     = true
}

variable "ssh_public_keys" {
  type = list(string)
}

variable "user_data" {
  description = "Optional cloud-init user-data."
  type        = string
  default     = ""
  nullable    = false
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
  type    = list(string)
  default = []
}
