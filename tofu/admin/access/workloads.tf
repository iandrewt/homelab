locals {
  sdn_path = "/sdn/zones/localnetwork/${local.cluster.bridge}"

  integrations = merge([
    for group, workload in local.cluster.workloads : {
      for name, integration in try(workload.integrations, {}) :
      "${group}-${name}" => {
        name    = name
        comment = integration.comment
        acls    = integration.acls
      }
    }
  ]...)

  # A Proxmox ACL grants one role on one path, so privileges that differ per path are
  # different roles. Integrations declare their own, unlike workload states, which all
  # want the same things and share the roles below.
  integration_roles = merge([
    for key, integration in local.integrations : {
      for path, privileges in integration.acls : "${key}|${path}" => {
        role_id    = "wl-${key}-${path == "/" ? "root" : replace(trimprefix(path, "/"), "/", "-")}"
        privileges = privileges
      }
    }
  ]...)

  workload_identities = {
    for group, workload in local.cluster.workloads : "wl-${group}" => {
      token_name = "tofu"
      comment    = workload.comment

      acls = [
        {
          path    = "/pool/${group}"
          role_id = proxmox_virtual_environment_role.tofu_workload.role_id
        },
        {
          path    = "/storage/${local.cluster.guest_defaults.disk_storage}"
          role_id = proxmox_virtual_environment_role.tofu_vm_storage.role_id
        },
        {
          path    = "/storage/${local.cluster.guest_defaults.snippet_storage}"
          role_id = proxmox_virtual_environment_role.tofu_vm_storage.role_id
        },
        {
          # The API requires SDN.Use on any bridge a VM attaches to,
          # plain Linux bridges included - they sit under the implicit localnetwork zone.
          path    = local.sdn_path
          role_id = proxmox_virtual_environment_role.tofu_sdn_use.role_id
        },
      ]
    }
  }

  integration_identities = {
    for key, integration in local.integrations : "wl-${key}" => {
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

  identities = merge(local.workload_identities, local.integration_identities)

  identity_acls = merge([
    for key, identity in local.identities : {
      for acl in identity.acls : "${key}|${acl.path}" => {
        identity = key
        path     = acl.path
        role_id  = acl.role_id
      }
    }
  ]...)
}

# --- Roles -------------------------------------------------------------------------

resource "proxmox_virtual_environment_role" "tofu_workload" {
  role_id = "TofuWorkload"

  privileges = [
    "VM.Allocate",
    "VM.Audit",
    "VM.Clone",
    "VM.Config.CDROM",
    "VM.Config.CPU",
    "VM.Config.Cloudinit",
    "VM.Config.Disk",
    "VM.Config.HWType",
    "VM.Config.Memory",
    "VM.Config.Network",
    "VM.Config.Options",
    "VM.Console",
    "VM.PowerMgmt",
    "VM.Snapshot",
    "VM.GuestAgent.Audit",
  ]
}

resource "proxmox_virtual_environment_role" "tofu_vm_storage" {
  role_id = "TofuVMStorage"

  privileges = [
    "Datastore.AllocateSpace",
    "Datastore.Audit",
  ]
}

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

resource "proxmox_virtual_environment_pool" "workload" {
  for_each = local.cluster.workloads

  pool_id = each.key
  comment = each.value.comment
}

resource "proxmox_virtual_environment_user" "identity" {
  for_each = local.identities

  user_id = "${each.key}@pve"
  comment = each.value.comment
  enabled = true
}

resource "proxmox_acl" "identity" {
  for_each = local.identity_acls

  user_id   = proxmox_virtual_environment_user.identity[each.value.identity].user_id
  path      = each.value.path
  role_id   = each.value.role_id
  propagate = true

  depends_on = [proxmox_virtual_environment_pool.workload]
}

module "identity_token" {
  source   = "../../modules/proxmox-identity"
  for_each = local.identities

  user_id    = proxmox_virtual_environment_user.identity[each.key].user_id
  token_name = each.value.token_name
  comment    = each.value.comment
  acls       = each.value.acls

  onepassword_vault = local.cluster.onepassword_vault
  item_title        = "${local.item_prefix} ${each.key} token"

  depends_on = [proxmox_acl.identity]
}
