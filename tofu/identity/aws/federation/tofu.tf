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
    profile      = "homelab-state-identity"
    key          = "identity/aws/federation/terraform.tfstate"
    use_lockfile = true
  }

  encryption {
    key_provider "aws_kms" "state" {
      kms_key_id         = "arn:aws:kms:ap-southeast-2:877127520873:alias/tofu-state"
      key_spec           = "AES_256"
      region             = "ap-southeast-2"
      profile            = "homelab-state-identity"
      encryption_context = { state = "identity/aws/federation/terraform.tfstate" }
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

  # The catalogue, read here since a provider's for_each can't take a module's output
  catalogue_dir = "${path.module}/../../../platform/aws/accounts"
  accounts      = toset([for f in fileset(local.catalogue_dir, "*.yaml") : trimsuffix(f, ".yaml")])
}

provider "aws" {
  region  = "ap-southeast-2"
  profile = local.profile
}

module "accounts" {
  source = "../../../modules/accounts"
}

# Through the bootstrap role, the only one the guardrails let change an identity provider
provider "aws" {
  alias    = "account"
  for_each = local.accounts

  region  = "ap-southeast-2"
  profile = local.profile

  assume_role {
    role_arn     = "arn:aws:iam::${module.accounts.ids[each.key]}:role/OrganizationAccountAccessRole"
    session_name = "federation"
  }

  default_tags {
    tags = { managed-by = "opentofu", layer = "identity/aws/federation" }
  }
}
