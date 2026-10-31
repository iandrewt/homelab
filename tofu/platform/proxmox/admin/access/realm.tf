data "azuread_client_config" "this" {}

# identity/entra/apps's
data "azuread_application" "this" {
  display_name = "Proxmox ${terraform.workspace}"
}

locals {
  realm = "entra"

  # A tenant's team gets what its tofu identity has
  oidc_grants = merge(
    {
      Administrators = [{ path = "/", role_id = "Administrator" }]
      Security       = [{ path = "/", role_id = "PVEAuditor" }]
    },
    { for tenant in keys(local.cluster.tenants) : module.tenancy.teams[tenant].title => local.tenant_identities["tenant-${tenant}"].acls },
  )
}

data "azuread_group" "oidc" {
  for_each = local.oidc_grants

  display_name     = each.key
  security_enabled = true
}

locals {
  # Proxmox names a claimed group `<value>-<realm>`, and Entra Free's values are object IDs
  oidc_groups = { for name, group in data.azuread_group.oidc : name => "${group.object_id}-${local.realm}" }

  oidc_group_acls = merge([
    for name, acls in local.oidc_grants : {
      for acl in acls : "${name}|${acl.path}|${acl.role_id}" => merge(acl, { group = name })
    }
  ]...)
}

# No client key: Proxmox always uses PKCE, so Entra registers it as a public client
resource "proxmox_realm_openid" "this" {
  realm      = local.realm
  issuer_url = "https://login.microsoftonline.com/${data.azuread_client_config.this.tenant_id}/v2.0"
  client_id  = data.azuread_application.this.client_id

  # Proxmox's name for preferred_username, so a user is <upn>@entra
  username_claim = "username"
  query_userinfo = false
  comment        = "Entra ID"

  groups_claim     = "groups"
  groups_overwrite = true

  autocreate = true
  default    = false
}

# A group nothing grants has no Proxmox group, so groups-overwrite drops it at sign-in
resource "proxmox_virtual_environment_group" "oidc" {
  for_each = local.oidc_groups

  group_id = each.value
  comment  = each.key
}

resource "proxmox_acl" "oidc" {
  for_each = local.oidc_group_acls

  group_id  = proxmox_virtual_environment_group.oidc[each.value.group].group_id
  path      = each.value.path
  role_id   = each.value.role_id
  propagate = true

  depends_on = [proxmox_virtual_environment_pool.tenant]
}
