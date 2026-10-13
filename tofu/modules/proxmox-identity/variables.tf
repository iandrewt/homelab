variable "user_id" {
  description = "Existing PVE user for the token, e.g. tofu@pve."
  type        = string
}

variable "token_name" {
  description = "Token name. Must be unique within the user."
  type        = string
}

variable "comment" {
  description = "Shown in the Proxmox UI."
  type        = string
  default     = "Managed by OpenTofu."
}

variable "acls" {
  description = "ACL entries granted to the token. Effective permission is this intersected with the user's."
  type = list(object({
    path      = string
    role_id   = string
    propagate = optional(bool, true)
  }))

  validation {
    condition     = length(var.acls) > 0
    error_message = "A privsep token with no ACL can do nothing at all - grant at least one."
  }
}

variable "onepassword_vault" {
  description = "1Password vault to write the token secret into."
  type        = string
}

variable "endpoint" {
  description = "The Proxmox API the token is for, recorded on its item."
  type        = string
}

variable "item_title" {
  description = "1Password item title."
  type        = string
}
