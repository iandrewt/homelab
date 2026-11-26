# What CI plans the Entra layers as. Applying them stays an admin's
resource "azuread_application" "tofu_plan" {
  display_name            = "Homelab Tofu Plan"
  sign_in_audience        = "AzureADMyOrg"
  prevent_duplicate_names = true
}

resource "azuread_service_principal" "tofu_plan" {
  client_id = azuread_application.tofu_plan.client_id
}

module "github" {
  source = "../../../modules/github"
}

# As AWS's tofu-plan trusts it, but for actor_id, which Entra can't match
resource "azuread_application_flexible_federated_identity_credential" "tofu_plan" {
  for_each = toset(["identity", "org"])

  application_id = azuread_application.tofu_plan.id
  display_name   = "tofu-plan-${each.key}"
  issuer         = "https://token.actions.githubusercontent.com"
  audience       = "api://AzureADTokenExchange"
  claims_matching_expression = join(" and ", [
    "claims['sub'] eq 'repo:${module.github.repository}:environment:tofu-plan-${each.key}'",
    "claims['repository_id'] eq '${module.github.repository_id}'",
    "claims['job_workflow_ref'] eq '${module.github.workflow}'",
  ])
}

# Not Graph's read permissions: an app reads a claims policy only with Policy.ReadWrite.ApplicationConfiguration
data "azuread_directory_role_templates" "this" {}

resource "azuread_directory_role_assignment" "tofu_plan" {
  role_id             = one([for t in data.azuread_directory_role_templates.this.role_templates : t.object_id if t.display_name == "Global Reader"])
  principal_object_id = azuread_service_principal.tofu_plan.object_id
}

data "azuread_service_principal" "graph" {
  client_id = "00000003-0000-0000-c000-000000000000"
}

# Provisioning's jobs and schema, which Global Reader doesn't reach for an app
resource "azuread_app_role_assignment" "tofu_plan_synchronization" {
  app_role_id         = data.azuread_service_principal.graph.app_role_ids["Synchronization.Read.All"]
  principal_object_id = azuread_service_principal.tofu_plan.object_id
  resource_object_id  = data.azuread_service_principal.graph.object_id
}
