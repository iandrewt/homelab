terraform {
  required_version = "~> 1.13"

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.114"
    }
    onepassword = {
      source  = "1Password/onepassword"
      version = "~> 3.3"
    }
    netbox = {
      source  = "e-breuninger/netbox"
      version = "~> 6.0"
    }
  }

  # A workspace per site
  backend "s3" {
    bucket               = "tofu-state-877127520873-ap-southeast-2-an"
    region               = "ap-southeast-2"
    profile              = "homelab-state-network"
    workspace_key_prefix = "network/dns"
    key                  = "terraform.tfstate"
    use_lockfile         = true
  }

  encryption {
    key_provider "aws_kms" "state" {
      kms_key_id         = "arn:aws:kms:ap-southeast-2:877127520873:alias/tofu-state"
      key_spec           = "AES_256"
      region             = "ap-southeast-2"
      profile            = "homelab-state-network"
      encryption_context = { state = "network/dns/${terraform.workspace}/terraform.tfstate" }
    }

    method "aes_gcm" "state" {
      keys = key_provider.aws_kms.state
    }

    state {
      method   = method.aes_gcm.state
      enforced = true
    }

    plan {
      method   = method.aes_gcm.state
      enforced = true
    }
  }
}

locals {
  site = terraform.workspace
  team = "network"

  # A provider's for_each is static, so the cells can't come from NetBox
  cells = toset(yamldecode(file("${path.module}/sites/${local.site}.yaml")).proxmox)
}

module "tenancy" {
  source = "../../modules/tenancy"
}

provider "onepassword" {}

data "onepassword_vault" "team" {
  name = module.tenancy.teams[local.team].title
}

ephemeral "onepassword_item" "netbox" {
  vault = data.onepassword_vault.team.uuid
  title = "NetBox API Token - Tofu"
}

provider "netbox" {
  server_url = ephemeral.onepassword_item.netbox.hostname
  api_token  = ephemeral.onepassword_item.netbox.credential
}

# What Platform vends a tenant of each cell: its grant, and its token
data "onepassword_item" "grant" {
  for_each = local.cells

  vault = data.onepassword_vault.team.uuid
  title = "Proxmox Grant - ${each.key}"
}

ephemeral "onepassword_item" "proxmox" {
  for_each = local.cells

  vault = data.onepassword_vault.team.uuid
  title = "Proxmox API Token - ${each.key} tenant-${local.team}"
}

locals {
  grants = {
    for cell, item in data.onepassword_item.grant : cell => {
      for label, field in item.section_map["cell"].field_map : label => nonsensitive(field.value)
    }
  }
}

provider "proxmox" {
  alias    = "cluster"
  for_each = local.cells

  endpoint  = local.grants[each.key]["endpoint"]
  insecure  = local.grants[each.key]["insecure"] == "true"
  api_token = ephemeral.onepassword_item.proxmox[each.key].password
}
