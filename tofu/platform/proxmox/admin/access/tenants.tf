locals {
  integrations = merge([
    for tenant, grant in local.cluster.tenants : {
      for name, integration in try(grant.integrations, {}) :
      "${tenant}-${name}" => {
        tenant  = tenant
        name    = name
        comment = integration.comment
        acls    = integration.acls
      }
    }
  ]...)

  # A Proxmox ACL grants one role on one path, so privileges that differ per path are
  # different roles. Integrations declare their own, unlike tenants, which all want the
  # same things and share built-in roles.
  integration_roles = merge([
    for key, integration in local.integrations : {
      for path, privileges in integration.acls : "${key}|${path}" => {
        role_id    = "tenant-${key}-${path == "/" ? "root" : replace(trimprefix(path, "/"), "/", "-")}"
        privileges = privileges
      }
    }
  ]...)

  # PVEPoolAdmin lets a tenant make its own workloads, as pools under this one
  tenant_identities = {
    for tenant, grant in local.cluster.tenants : "tenant-${tenant}" => {
      tenant     = tenant
      token_name = "tofu"
      comment    = grant.comment

      acls = concat([
        {
          path    = "/pool/${tenant}"
          role_id = "PVEVMAdmin"
        },
        {
          path    = "/pool/${tenant}"
          role_id = "PVEPoolAdmin"
        },
        {
          path    = "/storage/${local.cluster.guest_defaults.disk_storage}"
          role_id = "PVEDatastoreUser"
        },
        {
          path    = "/storage/${local.cluster.image_storage}"
          role_id = "PVEDatastoreUser"
        },
        # Proxmox wants Datastore.Allocate to attach a snippet to a VM at all
        {
          path    = "/storage/${local.cluster.guest_defaults.snippet_storage}"
          role_id = "PVEDatastoreAdmin"
        },
        ], [
        for vnet in try(grant.vnets, []) : {
          path    = "/sdn/zones/${local.cluster.site}/${vnet}"
          role_id = proxmox_virtual_environment_role.tofu_sdn_use.role_id
        }
        ], [
        for mapping in try(grant.mappings, []) : {
          path    = "/mapping/pci/${mapping}"
          role_id = "PVEMappingUser"
        }
      ])
    }
  }

  integration_identities = {
    for key, integration in local.integrations : "tenant-${key}" => {
      tenant     = integration.tenant
      token_name = integration.name
      comment    = integration.comment

      acls = [
        for path, privileges in integration.acls : {
          path    = path
          role_id = proxmox_virtual_environment_role.integration["${key}|${path}"].role_id
        }
      ]
    }
  }

  identities = merge(local.tenant_identities, local.integration_identities)

  identity_acls = merge([
    for key, identity in local.identities : {
      for acl in identity.acls : "${key}|${acl.path}|${acl.role_id}" => {
        identity = key
        path     = acl.path
        role_id  = acl.role_id
      }
    }
  ]...)
}

# --- Roles -------------------------------------------------------------------------

resource "proxmox_virtual_environment_role" "tofu_sdn_use" {
  role_id = "TofuSDNUse"

  privileges = ["SDN.Use"]
}

resource "proxmox_virtual_environment_role" "integration" {
  for_each = local.integration_roles

  role_id    = each.value.role_id
  privileges = each.value.privileges
}

# --- Pools, users, tokens ------------------------------------------------------------

# A tenant is a team, and its tokens go in the team's own vault
data "onepassword_vault" "tenant" {
  for_each = local.cluster.tenants

  name = title(each.key)
}

resource "proxmox_virtual_environment_pool" "tenant" {
  for_each = local.cluster.tenants

  pool_id = each.key
  comment = each.value.comment
}

resource "proxmox_virtual_environment_user" "identity" {
  for_each = local.identities

  user_id = "${each.key}@pve"
  comment = each.value.comment
  enabled = true

  # merge() keeps the last of two equal keys, and a role ID is only unique in Proxmox
  lifecycle {
    precondition {
      condition     = length(local.identities) == length(local.tenant_identities) + length(local.integration_identities)
      error_message = "A tenant and an integration both derive the same user - rename one."
    }
    precondition {
      condition     = length(distinct([for r in local.integration_roles : r.role_id])) == length(local.integration_roles)
      error_message = "Two integration ACL paths derive the same role ID - rename one."
    }
  }
}

resource "proxmox_acl" "identity" {
  for_each = local.identity_acls

  user_id   = proxmox_virtual_environment_user.identity[each.value.identity].user_id
  path      = each.value.path
  role_id   = each.value.role_id
  propagate = true

  lifecycle {
    precondition {
      condition     = !startswith(each.value.path, "/mapping/pci/") || contains(keys(try(local.cluster.mappings.pci, {})), trimprefix(each.value.path, "/mapping/pci/"))
      error_message = "${each.value.path} names no mapping in the cluster file's mappings.pci."
    }
  }

  depends_on = [proxmox_virtual_environment_pool.tenant]
}

module "identity_token" {
  source   = "../../../../modules/proxmox-identity"
  for_each = local.identities

  user_id    = proxmox_virtual_environment_user.identity[each.key].user_id
  token_name = each.value.token_name
  comment    = each.value.comment
  acls       = each.value.acls

  onepassword_vault = data.onepassword_vault.tenant[each.value.tenant].uuid
  item_title        = "Proxmox API Token - ${terraform.workspace} ${each.key}"
  endpoint          = local.cluster.endpoint

  depends_on = [proxmox_acl.identity]
}
