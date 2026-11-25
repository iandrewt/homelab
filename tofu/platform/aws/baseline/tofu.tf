terraform {
  required_version = "~> 1.13"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }

  backend "s3" {
    bucket       = "tofu-state-877127520873-ap-southeast-2-an"
    region       = "ap-southeast-2"
    profile      = "homelab-state-platform"
    key          = "platform/aws/baseline/terraform.tfstate"
    use_lockfile = true
  }

  encryption {
    key_provider "aws_kms" "state" {
      kms_key_id         = "arn:aws:kms:ap-southeast-2:877127520873:alias/tofu-state"
      key_spec           = "AES_256"
      region             = "ap-southeast-2"
      profile            = "homelab-state-platform"
      encryption_context = { state = "platform/aws/baseline/terraform.tfstate" }
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
  # The catalogue, read here since a provider's for_each can't take a module's output
  catalogue_dir = "${path.module}/../accounts"
  catalogue     = { for f in fileset(local.catalogue_dir, "*.yaml") : trimsuffix(f, ".yaml") => yamldecode(file("${local.catalogue_dir}/${f}")) }
  accounts      = toset(concat(["management"], keys(local.catalogue)))
}

locals {
  # Management's own administrator, and bootstrap in every member: what local-exec runs as too
  apply_profiles = { for slug in local.accounts : slug => slug == "management" ? "homelab-management" : "homelab-${slug}-bootstrap" }
}

provider "aws" {
  alias    = "account"
  for_each = local.accounts

  region  = "ap-southeast-2"
  profile = terraform.applying ? local.apply_profiles[each.key] : "homelab-${each.key}-plan"

  default_tags {
    tags = { managed-by = "opentofu", layer = "platform/aws/baseline" }
  }
}
