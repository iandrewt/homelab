# Auth: OP_SERVICE_ACCOUNT_TOKEN env var, picked up automatically - no explicit
# provider config needed.
provider "onepassword" {}

variable "onepassword_vault" {
  description = "1Password vault holding infrastructure credentials (name or UUID)."
  type        = string
}

data "onepassword_item" "proxmox_api_token" {
  vault = var.onepassword_vault
  title = "yggdrasil Proxmox API token" # placeholder title - rename once the item actually exists
  # Must be an "API Credential" category item in 1Password - that's what makes
  # .credential resolve. A Password-category item exposes .password instead.
}

data "onepassword_item" "control_ssh_key" {
  vault = var.onepassword_vault
  title = "homelab control node SSH key" # placeholder title - rename once the item actually exists
  # Must be an "SSH Key" category item - that's what makes .public_key resolve.
}
