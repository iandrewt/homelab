terraform {
  required_version = "~> 1.12"

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.114"
    }
    onepassword = {
      source  = "1Password/onepassword"
      version = "~> 3.3"
    }
  }

  # TODO: R2 or S3 once the bucket exists
  backend "local" {}

  encryption {
    key_provider "external" "primary" {
      command = ["uv", "run", "homelab", "tofu-key"]
    }

    method "aes_gcm" "primary" {
      keys = key_provider.external.primary
    }

    state {
      method   = method.aes_gcm.primary
      enforced = true
    }

    plan {
      method   = method.aes_gcm.primary
      enforced = true
    }
  }
}

locals {
  cluster = yamldecode(file("${path.module}/../../clusters/${terraform.workspace}.yaml"))
}

provider "onepassword" {}

ephemeral "onepassword_item" "token" {
  vault = local.cluster.onepassword_vault
  title = "${local.cluster.onepassword_item_prefix} images token"
}

provider "proxmox" {
  endpoint  = local.cluster.endpoint
  api_token = ephemeral.onepassword_item.token.password
  insecure  = true # self-signed until there's an ACME account

  ssh {
    agent    = true
    username = local.cluster.ssh_username
  }
}
