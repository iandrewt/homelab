variable "prefix_roles" {
  type        = list(string)
  default     = []
  description = "Prefix roles to read the site's prefixes for, by name - the provider can look a role up by nothing else. The output is keyed by slug."
}

variable "site_slug" {
  type        = string
  description = "NetBox site slug. The natural key everything else is resolved from."
}
