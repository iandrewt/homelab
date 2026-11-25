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
    profile      = "homelab-state-security"
    key          = "security/aws/logging/terraform.tfstate"
    use_lockfile = true
  }

  encryption {
    key_provider "aws_kms" "state" {
      kms_key_id         = "arn:aws:kms:ap-southeast-2:877127520873:alias/tofu-state"
      key_spec           = "AES_256"
      region             = "ap-southeast-2"
      profile            = "homelab-state-security"
      encryption_context = { state = "security/aws/logging/terraform.tfstate" }
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
  profile = terraform.applying ? "homelab-log-archive-bootstrap" : "homelab-log-archive-plan"

  default_tags {
    tags = { managed-by = "opentofu", layer = "security/aws/logging" }
  }
}

provider "aws" {
  alias   = "security"
  region  = "ap-southeast-2"
  profile = terraform.applying ? "homelab-security-tooling-bootstrap" : "homelab-security-tooling-plan"

  default_tags {
    tags = { managed-by = "opentofu", layer = "security/aws/logging" }
  }
}
