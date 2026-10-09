variable "node_name" {
  type = string
}

variable "vm_id" {
  description = "Proxmox VMID. Must be unique in the cluster. Nothing derives from it."
  type        = number

  validation {
    condition     = var.vm_id >= 100
    error_message = "Proxmox reserves VMIDs below 100."
  }
}

variable "hostname" {
  type = string
}

variable "region" {
  description = "Reported as meta-data region. The cluster, matching what proxmox-cloud-controller-manager calls a region."
  type        = string
  default     = null
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

variable "vnet" {
  description = "The SDN VNet the NIC attaches to."
  type        = string
}

variable "dhcp4" {
  description = "Ask for an IPv4 lease."
  type        = bool
  default     = true
}

variable "dhcp6" {
  description = "Ask for an IPv6 lease."
  type        = bool
  default     = true
}

variable "ipv4_address" {
  description = "Static IPv4 in CIDR notation. Null when dhcp4 is on."
  type        = string
  default     = null
}

variable "ipv6_address" {
  description = "Static IPv6 in CIDR notation. Prefer ipv6_token unless something binds to the address at boot."
  type        = string
  default     = null
}

variable "ipv6_token" {
  description = "SLAAC interface identifier, e.g. \"::11\". Yields <prefix>::11 under every advertised prefix."
  type        = string
  default     = null

  validation {
    condition     = var.ipv6_token == null || can(regex("^::[0-9a-fA-F:]+$", var.ipv6_token))
    error_message = "ipv6_token must be an interface identifier written as ::n."
  }
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

variable "dns_servers" {
  description = "DNS servers to use instead of the lease's. Only a resolver should need this."
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
  description = "Only on an existing VM for a pool-scoped token: Proxmox checks tags at create before the VM joins its pool."
  type        = list(string)
  default     = []
}
