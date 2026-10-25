terraform {
  required_providers {
    azuread = {
      source = "hashicorp/azuread"
    }
  }
}

variable "display_name" {
  type        = string
  description = "`<Platform> <instance>`: what consumers find it by."
}

variable "logo" {
  type        = string
  description = "A PNG, 215x215, under 100 KB."
}

variable "redirect_uris" {
  type        = list(string)
  description = "Public-client redirect URIs: it signs in with PKCE, so has no secret."
}

variable "users" {
  type        = set(string)
  description = "Object IDs of the users who may sign in."
}

resource "azuread_application" "this" {
  display_name            = var.display_name
  logo_image              = filebase64(var.logo)
  sign_in_audience        = "AzureADMyOrg"
  prevent_duplicate_names = true

  # Object IDs: names need a group assigned to the app, which is P1
  group_membership_claims = ["SecurityGroup"]

  optional_claims {
    id_token {
      name = "groups"
    }
  }

  public_client {
    redirect_uris = var.redirect_uris
  }
}

resource "azuread_service_principal" "this" {
  client_id                    = azuread_application.this.client_id
  app_role_assignment_required = true
}

# Gates sign-in; groups grant. With P1, assign the groups instead.
resource "azuread_app_role_assignment" "this" {
  for_each = var.users

  app_role_id         = "00000000-0000-0000-0000-000000000000"
  principal_object_id = each.key
  resource_object_id  = azuread_service_principal.this.object_id
}

output "client_id" {
  value = azuread_application.this.client_id
}
