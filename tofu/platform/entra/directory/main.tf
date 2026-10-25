# Members are people, and stay in Entra: the repo is public
resource "azuread_group_without_members" "this" {
  for_each = module.tenancy.groups

  display_name            = each.key
  description             = each.value
  security_enabled        = true
  prevent_duplicate_names = true
}

# Owns the `aws` extension: provisioning's scoping filters can't test membership, but can read it
resource "azuread_application" "directory" {
  display_name            = "Homelab Directory"
  sign_in_audience        = "AzureADMyOrg"
  prevent_duplicate_names = true
}

resource "azuread_service_principal" "directory" {
  client_id = azuread_application.directory.client_id
}

resource "msgraph_resource" "aws_extension" {
  url = "applications/${azuread_application.directory.object_id}/extensionProperties"
  body = {
    name          = "aws"
    dataType      = "String"
    targetObjects = ["User", "Group"]
  }

  depends_on = [azuread_service_principal.directory]
}
