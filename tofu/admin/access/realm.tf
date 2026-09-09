locals {
  oidc_realms = try({ (local.cluster.oidc.realm) = local.cluster.oidc }, {})
}

data "onepassword_item" "oidc_client_secret" {
  for_each = local.oidc_realms

  vault = local.cluster.onepassword_vault
  title = "${local.item_prefix} OIDC client secret"
}

resource "proxmox_realm_openid" "this" {
  for_each = local.oidc_realms

  realm      = each.key
  issuer_url = each.value.issuer_url
  client_id  = each.value.client_id
  client_key = data.onepassword_item.oidc_client_secret[each.key].password

  username_claim = try(each.value.username_claim, "email")
  comment        = "Managed by OpenTofu."

  autocreate = true
  default    = false
}
