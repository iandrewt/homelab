# Adopts the assignment made by hand. Delete after the first apply.

data "msgraph_resource" "aws_assignments" {
  url                    = "servicePrincipals/${local.aws_sp}/appRoleAssignedTo"
  response_export_values = { all = "value[].{id: id, principal: principalId}" }
}

import {
  for_each = { for a in data.msgraph_resource.aws_assignments.output.all : a.principal => a.id }

  to = azuread_app_role_assignment.aws[each.key]
  id = "/servicePrincipals/${local.aws_sp}/appRoleAssignedTo/${each.value}"
}
