# Adopts the groups made by hand. Delete after the first apply.

data "azuread_group" "existing" {
  for_each = toset(["Administrators", "Network", "Platform", "Security"])

  display_name     = each.key
  security_enabled = true
}

import {
  for_each = data.azuread_group.existing

  to = azuread_group_without_members.this[each.key]
  id = "/groups/${each.value.object_id}"
}
