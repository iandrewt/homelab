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
    key          = "platform/aws/org/terraform.tfstate"
    use_lockfile = true
  }

  encryption {
    key_provider "aws_kms" "state" {
      kms_key_id         = "arn:aws:kms:ap-southeast-2:585242447535:alias/tofu-state"
      key_spec           = "AES_256"
      region             = "ap-southeast-2"
      profile            = "homelab-state"
      encryption_context = { state = "platform/aws/org/terraform.tfstate" }
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
  region  = "ap-southeast-2"
  profile = "homelab-management"

  default_tags {
    tags = { managed-by = "opentofu", layer = "platform/aws/org" }
  }
}
