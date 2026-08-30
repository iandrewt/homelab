variable "proxmox_endpoint" {
  description = "Override with -var for anything else, e.g. a playground VM."
  type        = string
  default     = "https://yggdrasil.alfheim.titmuss.zone:8006/"
}

provider "proxmox" {
  endpoint  = var.proxmox_endpoint
  api_token = data.onepassword_item.proxmox_api_token.credential
  insecure  = true # self-signed cert until Proxmox has its own ACME-issued certificate

  ssh {
    agent    = true
    username = "root"
  }
}
