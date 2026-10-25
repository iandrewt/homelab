variable "prefix_roles" {
  type        = list(string)
  default     = []
  description = "Prefix roles to read, by name: the provider can look a role up by nothing else."
}

variable "origin_asn" {
  type        = number
  default     = null
  description = "The speaker group whose blocks (`owned`) and assigned addresses (`assigned`) to read."
}

variable "site_slug" {
  type        = string
  description = "NetBox site slug."
}

variable "trust_tags" {
  type        = set(string)
  default     = []
  description = "Tag slugs to collect `trusted` for."
}

variable "destinations" {
  type        = map(string)
  default     = {}
  description = "Addresses by a name of the consumer's: `audiences` gives each the sources its audience admits, from the most specific prefix holding it."
}

variable "audience_roles" {
  type        = set(string)
  default     = []
  description = "Prefix role slugs, each of them in `prefix_roles` by name: `audiences` gives each the sources its prefixes' audience admits."
}
