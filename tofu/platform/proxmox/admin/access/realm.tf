locals {
  oidc_realms = try({ (local.cluster.oidc.realm) = local.cluster.oidc }, {})

  # The app roles a user is assigned in Entra, which Proxmox suffixes with the realm. Each
  # tenant's team gets what its tofu identity has.
  oidc_groups = merge([
    for realm in keys(local.oidc_realms) : merge(
      {
        "administrator-${realm}" = [{ path = "/", role_id = "Administrator" }]
        "security-${realm}"      = [{ path = "/", role_id = "PVEAuditor" }]
      },
      { for tenant in keys(local.cluster.tenants) : "${tenant}-${realm}" => local.tenant_identities["tenant-${tenant}"].acls },
    )
  ]...)

  oidc_group_acls = merge([
    for group, acls in local.oidc_groups : {
      for acl in acls : "${group}|${acl.path}|${acl.role_id}" => merge(acl, { group = group })
    }
  ]...)
}

# No client key: Proxmox always uses PKCE, so Entra registers it as a public client
resource "proxmox_realm_openid" "this" {
  for_each = local.oidc_realms

  realm      = each.key
  issuer_url = each.value.issuer_url
  client_id  = each.value.client_id

  username_claim = try(each.value.username_claim, "username")
  query_userinfo = false
  comment        = each.value.comment

  groups_claim     = "roles"
  groups_overwrite = true

  autocreate = true
  default    = false
}

resource "proxmox_virtual_environment_group" "oidc" {
  for_each = local.oidc_groups

  group_id = each.key
}

resource "proxmox_acl" "oidc" {
  for_each = local.oidc_group_acls

  group_id  = proxmox_virtual_environment_group.oidc[each.value.group].group_id
  path      = each.value.path
  role_id   = each.value.role_id
  propagate = true

  depends_on = [proxmox_virtual_environment_pool.tenant]
}
