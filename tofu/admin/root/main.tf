locals {
  admin_user_id = "tofu@pve"
}

resource "proxmox_virtual_environment_user" "tofu" {
  user_id = local.admin_user_id
  comment = "OpenTofu cluster administration."
  enabled = true
}

# PVEAdmin lacks both Permissions.Modify and Sys.AccessNetwork
resource "proxmox_acl" "tofu" {
  user_id   = proxmox_virtual_environment_user.tofu.user_id
  path      = "/"
  role_id   = "Administrator"
  propagate = true
}

resource "proxmox_virtual_environment_role" "tofu_access" {
  role_id = "TofuAccess"

  privileges = [
    "Permissions.Modify",
    "Sys.Modify",
    "Sys.Audit",
    "Realm.Allocate",
    "Realm.AllocateUser",
    "User.Modify",
    "Group.Allocate",
    "Pool.Allocate",
  ]
}

module "access_token" {
  source = "../../modules/proxmox-identity"

  user_id    = proxmox_virtual_environment_user.tofu.user_id
  token_name = "access"
  comment    = "Access control: users, roles, pools, realms, tokens."

  acls = [{
    path    = "/"
    role_id = proxmox_virtual_environment_role.tofu_access.role_id
  }]

  onepassword_vault = data.onepassword_vault.this.uuid
  item_title        = "${local.cluster.onepassword_item_prefix} access token"

  depends_on = [proxmox_acl.tofu]
}
