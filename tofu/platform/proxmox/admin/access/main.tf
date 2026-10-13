locals {
  admin_user_id = "tofu@pve"
}

module "cluster_token" {
  source = "../../../../modules/proxmox-identity"

  user_id    = local.admin_user_id
  token_name = "cluster"
  comment    = "Cluster layer: cluster options, node config, SDN, storage, mappings."

  acls = [{
    path    = "/"
    role_id = proxmox_virtual_environment_role.tofu_cluster.role_id
  }]

  onepassword_vault = data.onepassword_vault.this.uuid
  item_title        = "Proxmox API Token - ${terraform.workspace} cluster"
  endpoint          = local.cluster.endpoint
}

module "images_token" {
  source = "../../../../modules/proxmox-identity"

  user_id    = local.admin_user_id
  token_name = "images"
  comment    = "Images layer: downloads cloud images."

  acls = [
    {
      path    = "/storage/${local.cluster.image_storage}"
      role_id = proxmox_virtual_environment_role.tofu_images.role_id
    },
    {
      path    = "/nodes"
      role_id = proxmox_virtual_environment_role.tofu_network_fetch.role_id
    },
  ]

  onepassword_vault = data.onepassword_vault.this.uuid
  item_title        = "Proxmox API Token - ${terraform.workspace} images"
  endpoint          = local.cluster.endpoint
}
