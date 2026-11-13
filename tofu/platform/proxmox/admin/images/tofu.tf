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
    bucket               = "tofu-state-877127520873-ap-southeast-2-an"
    region               = "ap-southeast-2"
    profile              = "homelab-state-platform"
    workspace_key_prefix = "platform/proxmox/admin/images"
    key                  = "terraform.tfstate"
    use_lockfile         = true
  }

  encryption {
    key_provider "aws_kms" "state" {
      kms_key_id         = "arn:aws:kms:ap-southeast-2:877127520873:alias/tofu-state"
      key_spec           = "AES_256"
      region             = "ap-southeast-2"
      profile            = "homelab-state-platform"
      encryption_context = { state = "platform/proxmox/admin/images/${terraform.workspace}/terraform.tfstate" }
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

# A workspace per site: every cluster there mounts the library, so one downloads it
locals {
  library = yamldecode(file("${path.module}/../../images/${terraform.workspace}.yaml"))

  clusters = {
    for f in fileset("${path.module}/../../clusters", "*.yaml") :
    trimsuffix(f, ".yaml") => yamldecode(file("${path.module}/../../clusters/${f}"))
  }
  fetch_cluster = sort([for name, c in local.clusters : name if c.site == terraform.workspace])[0]
  cluster       = local.clusters[local.fetch_cluster]
}

provider "onepassword" {}

data "onepassword_vault" "this" {
  name = local.cluster.onepassword_vault
}

ephemeral "onepassword_item" "token" {
  vault = data.onepassword_vault.this.uuid
  title = "Proxmox API Token - ${local.fetch_cluster} images"
}

provider "proxmox" {
  endpoint  = local.cluster.endpoint
  insecure  = try(local.cluster.insecure, false)
  api_token = ephemeral.onepassword_item.token.password
}
