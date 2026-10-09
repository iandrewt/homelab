terraform {
  required_version = "~> 1.13"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }

  backend "s3" {
    bucket       = "tofu-state-585242447535-ap-southeast-2-an"
    region       = "ap-southeast-2"
    profile      = "homelab-state"
    key          = "platform/aws/baseline/terraform.tfstate"
    use_lockfile = true
  }

  encryption {
    key_provider "aws_kms" "state" {
      kms_key_id         = "arn:aws:kms:ap-southeast-2:585242447535:alias/tofu-state"
      key_spec           = "AES_256"
      region             = "ap-southeast-2"
      profile            = "homelab-state"
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
  profile = "homelab-management"

  # As org names them: a provider's for_each can't read anything, so they're here too
  accounts = toset(["Management", "Log Archive", "Security", "Network", "Platform", "Sandbox"])
}

provider "aws" {
  region  = "ap-southeast-2"
  profile = local.profile
}

data "aws_organizations_organization" "this" {}

locals {
  account_ids = merge(
    { for account in data.aws_organizations_organization.this.non_master_accounts : account.name => account.id },
    { Management = data.aws_organizations_organization.this.master_account_id },
  )

  bootstrap_roles = {
    for name in local.accounts :
    name => "arn:aws:iam::${local.account_ids[name]}:role/OrganizationAccountAccessRole" if name != "Management"
  }
}

provider "aws" {
  alias    = "account"
  for_each = local.accounts

  region  = "ap-southeast-2"
  profile = local.profile

  dynamic "assume_role" {
    for_each = each.key == "Management" ? [] : [each.key]

    content {
      role_arn     = local.bootstrap_roles[assume_role.value]
      session_name = "baseline"
    }
  }

  default_tags {
    tags = { managed-by = "opentofu", layer = "platform/aws/baseline" }
  }
}
