variable "prefix_roles" {
  type        = list(string)
  default     = []
  description = "Prefix roles to read the site's prefixes for, by name - a purpose's block, or a container like `pods`."
}

variable "site_slug" {
  type        = string
  description = "NetBox site slug. The natural key everything else is resolved from."
}
