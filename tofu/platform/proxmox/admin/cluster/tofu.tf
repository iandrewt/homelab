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

  backend "s3" {
    bucket               = "tofu-state-585242447535-ap-southeast-2-an"
    region               = "ap-southeast-2"
    profile              = "homelab-state"
    workspace_key_prefix = "platform/proxmox/admin/cluster"
    key                  = "terraform.tfstate"
    use_lockfile         = true
  }

  encryption {
    key_provider "aws_kms" "state" {
      kms_key_id         = "arn:aws:kms:ap-southeast-2:585242447535:alias/tofu-state"
      key_spec           = "AES_256"
      region             = "ap-southeast-2"
      profile            = "homelab-state"
      encryption_context = { state = "platform/proxmox/admin/cluster/${terraform.workspace}/terraform.tfstate" }
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
  cluster = yamldecode(file("${path.module}/../../clusters/${terraform.workspace}.yaml"))
}

provider "netbox" {
  server_url = ephemeral.onepassword_item.netbox.hostname
  api_token  = ephemeral.onepassword_item.netbox.credential
}

module "netbox" {
  source = "../../../../modules/netbox-site"

  site_slug = local.cluster.site
}

provider "onepassword" {}

data "onepassword_vault" "this" {
  name = local.cluster.onepassword_vault
}

ephemeral "onepassword_item" "netbox" {
  vault = data.onepassword_vault.this.uuid
  title = "Tofu NetBox API Token"
}

ephemeral "onepassword_item" "token" {
  vault = data.onepassword_vault.this.uuid
  title = "${local.cluster.onepassword_item_prefix} cluster token"
}

provider "proxmox" {
  endpoint  = local.cluster.endpoint
  api_token = ephemeral.onepassword_item.token.password

  ssh {
    agent    = true
    username = local.cluster.ssh_username
  }
}
