terraform {
  required_version = ">= 1.12.0"

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.112"
    }
    onepassword = {
      source  = "1Password/onepassword"
      version = "~> 3.3"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }

  # TODO: R2 or S3 once the bucket exists
  backend "local" {}

  encryption {
    key_provider "pbkdf2" "primary" {
      passphrase = var.tofu_state_passphrase
    }

    method "aes_gcm" "primary" {
      keys = key_provider.pbkdf2.primary
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

variable "tofu_state_passphrase" {
  description = "State/plan encryption passphrase. Supply as TF_VAR_tofu_state_passphrase, never a .tfvars file."
  type        = string
  sensitive   = true
}

locals {
  cluster_name = "alfheim"
  pool         = "platform"

  cluster = yamldecode(file("${path.module}/../../../clusters/${local.cluster_name}.yaml"))
  ipam    = jsondecode(file("${path.module}/../../../clusters/generated/${local.cluster_name}.json"))
}

provider "onepassword" {}

ephemeral "onepassword_item" "token" {
  vault = local.cluster.onepassword_vault
  title = "${local.cluster.onepassword_item_prefix} wl-platform token"
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
