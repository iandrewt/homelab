terraform {
  required_version = ">= 1.12.0"

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
    key          = "aws/identity.tfstate"
    use_lockfile = true
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

provider "aws" {
  alias   = "management"
  region  = "ap-southeast-2"
  profile = "homelab-management"
}

data "aws_organizations_organization" "this" {
  provider = aws.management
}

locals {
  account_ids = {
    for account in data.aws_organizations_organization.this.non_master_accounts :
    account.name => account.id if account.state == "ACTIVE"
  }
}

# Through the bootstrap role rather than a permission set: what grants access can't depend
# on it
provider "aws" {
  region  = "ap-southeast-2"
  profile = "homelab-management"

  assume_role {
    role_arn     = "arn:aws:iam::${local.account_ids["Security"]}:role/OrganizationAccountAccessRole"
    session_name = "identity"
  }

  default_tags {
    tags = { managed-by = "opentofu", layer = "aws/identity" }
  }
}
