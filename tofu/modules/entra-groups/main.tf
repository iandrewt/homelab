terraform {
  required_providers {
    azuread = {
      source = "hashicorp/azuread"
    }
  }
}

variable "names" {
  type        = set(string)
  description = "Groups to find, by display name. One that doesn't exist yet is left out, with no members."
}

# By name: read through directory's resources, it would wait for apply whenever one changes
data "azuread_groups" "this" {
  display_names    = var.names
  security_enabled = true
  ignore_missing   = true
}

data "azuread_group" "this" {
  for_each = { for g in data.azuread_groups.this.groups : g.display_name => g.object_id }

  object_id = each.value
}

# Direct members only, as provisioning reads them
data "azuread_users" "this" {
  for_each = data.azuread_group.this

  object_ids     = each.value.members
  ignore_missing = true
}

output "object_ids" {
  description = "Each group that exists, by display name."
  value       = { for name, g in data.azuread_group.this : name => g.object_id }
}

output "users" {
  description = "Each group's direct members that are users, by display name."
  value       = { for name in var.names : name => try(data.azuread_users.this[name].users[*].object_id, []) }
}
