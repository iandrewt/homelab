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

  backend "s3" {
    bucket               = "tofu-state-585242447535-ap-southeast-2-an"
    region               = "ap-southeast-2"
    profile              = "homelab-state"
    workspace_key_prefix = "clusters"
    key                  = "admin/root.tfstate"
    use_lockfile         = true
  }

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

data "onepassword_vault" "this" {
  name = local.cluster.onepassword_vault
}

provider "proxmox" {
  endpoint = local.cluster.endpoint
  username = "root@pam"
  insecure = true # self-signed until there's an ACME account
}
