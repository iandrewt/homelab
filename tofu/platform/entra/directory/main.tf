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

  # Graph reads the name back as extension_<client id>_aws, and an extension can't be updated
  lifecycle {
    ignore_changes = [body]
  }
}

# Groups and apps are found by name, so only an admin may make them, consenting to an app
# included, and an admin's password is reset by another admin. Set by hand: writing these takes
# Policy.ReadWrite.Authorization, which the Azure CLI can't be granted.
check "only_admins_make_directory_objects" {
  data "msgraph_resource" "authorization" {
    url = "policies/authorizationPolicy"
    response_export_values = {
      invites    = "allowInvitesFrom"
      guest_role = "guestUserRoleId"
      open = join(" || ", [
        "allowedToUseSSPR",
        "defaultUserRolePermissions.allowedToCreateApps",
        "defaultUserRolePermissions.allowedToCreateSecurityGroups",
        "defaultUserRolePermissions.allowedToCreateTenants",
      ])
      sign_up = "allowEmailVerifiedUsersToJoinOrganization || allowedToSignUpEmailBasedSubscriptions"
      consent = "defaultUserRolePermissions.permissionGrantPoliciesAssigned[?starts_with(@, 'ManagePermissionGrantsForSelf.')]"
    }
  }

  assert {
    condition     = !data.msgraph_resource.authorization.output.open && contains(["none", "adminsAndGuestInviters"], data.msgraph_resource.authorization.output.invites)
    error_message = "Users can make directory objects: in the Entra admin center, turn off app registration, security group creation, tenant creation and admins' self-service password reset, and let only admins and Guest Inviters invite."
  }

  assert {
    condition     = !data.msgraph_resource.authorization.output.sign_up
    error_message = "Anyone with a verified email can join the tenant: no screen sets this, so PATCH policies/authorizationPolicy with allowEmailVerifiedUsersToJoinOrganization and allowedToSignUpEmailBasedSubscriptions false."
  }

  assert {
    condition     = data.msgraph_resource.authorization.output.guest_role == "2af84b1e-32c8-42b7-82bc-daa82404023b"
    error_message = "Guests can read the directory: in External collaboration settings, restrict guests to their own directory objects."
  }

  assert {
    condition     = length(data.msgraph_resource.authorization.output.consent) == 0
    error_message = "Users can consent to apps: once entra/apps has granted each app's consent, set user consent to Do not allow."
  }
}

# Microsoft 365 groups are Group.Unified's, a settings object made from its template
check "only_admins_make_microsoft_365_groups" {
  data "msgraph_resource" "group_settings" {
    url = "groupSettings"
    response_export_values = {
      creation = "value[?templateId == '62375ab9-6b52-47ed-826b-58e47e0e304b'].values[] | [?name == 'EnableGroupCreation'].value"
    }
  }

  assert {
    condition     = data.msgraph_resource.group_settings.output.creation == ["false"]
    error_message = "Users can create Microsoft 365 groups: turn it off in the Entra admin center's group settings."
  }
}
