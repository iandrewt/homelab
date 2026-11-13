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
      version = "~> 5.8"
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

  # Every cluster at the site granting the team a share: a provider can't come from NetBox
  proxmox = {
    for f in fileset("${path.module}/../../platform/proxmox/clusters", "*.yaml") :
    trimsuffix(f, ".yaml") => yamldecode(file("${path.module}/../../platform/proxmox/clusters/${f}"))
    if yamldecode(file("${path.module}/../../platform/proxmox/clusters/${f}")).site == local.site &&
    contains(keys(yamldecode(file("${path.module}/../../platform/proxmox/clusters/${f}")).tenants), local.team)
  }
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

ephemeral "onepassword_item" "proxmox" {
  for_each = local.proxmox

  vault = data.onepassword_vault.team.uuid
  title = "Proxmox API Token - ${each.key} tenant-${local.team}"
}

provider "proxmox" {
  alias    = "cluster"
  for_each = local.proxmox

  endpoint  = each.value.endpoint
  insecure  = try(each.value.insecure, false)
  api_token = ephemeral.onepassword_item.proxmox[each.key].password
}
