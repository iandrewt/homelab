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
    key          = "aws/logging.tfstate"
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
  region  = "ap-southeast-2"
  profile = "homelab-log-archive"

  default_tags {
    tags = { managed-by = "opentofu", layer = "aws/logging" }
  }
}

provider "aws" {
  alias   = "security"
  region  = "ap-southeast-2"
  profile = "homelab-security"

  default_tags {
    tags = { managed-by = "opentofu", layer = "aws/logging" }
  }
}
