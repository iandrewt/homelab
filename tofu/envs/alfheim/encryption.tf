# TODO: wire up once the passphrase exists in 1Password. Never a .tfvars file.
# Rotation: keep the old key_provider as a `fallback` method rather than deleting it.

variable "tofu_state_passphrase" {
  description = "State/plan encryption passphrase. Source: 1Password, via TF_VAR_tofu_state_passphrase. Never in a .tfvars file."
  type        = string
  sensitive   = true
  default     = null
}

# terraform {
#   encryption {
#     key_provider "pbkdf2" "primary" {
#       passphrase = var.tofu_state_passphrase
#     }
#
#     method "aes_gcm" "primary" {
#       keys = key_provider.pbkdf2.primary
#     }
#
#     state {
#       method = method.aes_gcm.primary
#     }
#
#     plan {
#       method = method.aes_gcm.primary
#     }
#   }
# }
