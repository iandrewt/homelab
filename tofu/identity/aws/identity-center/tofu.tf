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
    key          = "identity/aws/identity-center/terraform.tfstate"
    use_lockfile = true
  }

  encryption {
    key_provider "aws_kms" "state" {
      kms_key_id         = "arn:aws:kms:ap-southeast-2:877127520873:alias/tofu-state"
      key_spec           = "AES_256"
      region             = "ap-southeast-2"
      profile            = "homelab-state-identity"
      encryption_context = { state = "identity/aws/identity-center/terraform.tfstate" }
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

provider "aws" {
  alias   = "management"
  region  = "ap-southeast-2"
  profile = "homelab-management"
}

module "accounts" {
  source = "../../../modules/accounts"

  providers = { aws = aws.management }
}

provider "aws" {
  region  = "ap-southeast-2"
  profile = "homelab-management"

  assume_role {
    role_arn     = "arn:aws:iam::${module.accounts.ids["identity"]}:role/OrganizationAccountAccessRole"
    session_name = "identity"
  }

  default_tags {
    tags = { managed-by = "opentofu", layer = "identity/aws/identity-center" }
  }
}
