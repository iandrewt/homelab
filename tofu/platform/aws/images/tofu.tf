terraform {
  required_version = "~> 1.13"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
    onepassword = {
      source  = "1Password/onepassword"
      version = "~> 3.3"
    }
    # 6.0, which alone reads aggregates; the other layers stay on 5.8 until it settles
    netbox = {
      source  = "e-breuninger/netbox"
      version = "6.0.1"
    }
  }

  backend "s3" {
    bucket       = "tofu-state-877127520873-ap-southeast-2-an"
    region       = "ap-southeast-2"
    profile      = "homelab-state-platform"
    key          = "platform/aws/images/terraform.tfstate"
    use_lockfile = true
  }

  encryption {
    key_provider "aws_kms" "state" {
      kms_key_id         = "arn:aws:kms:ap-southeast-2:877127520873:alias/tofu-state"
      key_spec           = "AES_256"
      region             = "ap-southeast-2"
      profile            = "homelab-state-platform"
      encryption_context = { state = "platform/aws/images/terraform.tfstate" }
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
  profile = "homelab-platform"

  default_tags {
    tags = { managed-by = "opentofu", layer = "platform/aws/images" }
  }
}

provider "onepassword" {}

data "onepassword_vault" "platform" {
  name = "Platform"
}

ephemeral "onepassword_item" "netbox" {
  vault = data.onepassword_vault.platform.uuid
  title = "NetBox API Token - Tofu"
}

provider "netbox" {
  server_url = ephemeral.onepassword_item.netbox.hostname
  api_token  = ephemeral.onepassword_item.netbox.credential
}
