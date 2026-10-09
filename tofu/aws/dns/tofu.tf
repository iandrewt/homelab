terraform {
  required_version = ">= 1.12.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.26"
    }
    onepassword = {
      source  = "1Password/onepassword"
      version = "~> 3.3"
    }
  }

  backend "s3" {
    bucket       = "tofu-state-585242447535-ap-southeast-2-an"
    region       = "ap-southeast-2"
    profile      = "homelab-state"
    key          = "aws/dns.tfstate"
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
  profile = "homelab-network"

  default_tags {
    tags = { managed-by = "opentofu", layer = "aws/dns" }
  }
}

provider "onepassword" {}

data "onepassword_vault" "homelab" {
  name = "Homelab"
}

ephemeral "onepassword_item" "cloudflare" {
  vault = data.onepassword_vault.homelab.uuid
  title = "Tofu Cloudflare API token"
}

provider "cloudflare" {
  api_token = ephemeral.onepassword_item.cloudflare.credential
}
