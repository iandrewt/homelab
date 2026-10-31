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
  }

  backend "s3" {
    bucket               = "tofu-state-585242447535-ap-southeast-2-an"
    region               = "ap-southeast-2"
    profile              = "homelab-state-platform"
    workspace_key_prefix = "platform/proxmox/admin/root"
    key                  = "terraform.tfstate"
    use_lockfile         = true
  }

  encryption {
    key_provider "aws_kms" "state" {
      kms_key_id         = "arn:aws:kms:ap-southeast-2:585242447535:alias/tofu-state"
      key_spec           = "AES_256"
      region             = "ap-southeast-2"
      profile            = "homelab-state-platform"
      encryption_context = { state = "platform/proxmox/admin/root/${terraform.workspace}/terraform.tfstate" }
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

provider "onepassword" {}

data "onepassword_vault" "this" {
  name = local.cluster.onepassword_vault
}

provider "proxmox" {
  endpoint = local.cluster.endpoint
  insecure = try(local.cluster.insecure, false)
  username = "root@pam"
}
