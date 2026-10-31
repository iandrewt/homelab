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
    netbox = {
      source  = "e-breuninger/netbox"
      version = "~> 5.8"
    }
  }

  backend "s3" {
    bucket       = "tofu-state-877127520873-ap-southeast-2-an"
    region       = "ap-southeast-2"
    profile      = "homelab-state-network"
    key          = "network/aws/dns/terraform.tfstate"
    use_lockfile = true
  }

  encryption {
    key_provider "aws_kms" "state" {
      kms_key_id         = "arn:aws:kms:ap-southeast-2:877127520873:alias/tofu-state"
      key_spec           = "AES_256"
      region             = "ap-southeast-2"
      profile            = "homelab-state-network"
      encryption_context = { state = "network/aws/dns/terraform.tfstate" }
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
  profile = "homelab-network"

  default_tags {
    tags = { managed-by = "opentofu", layer = "network/aws/dns" }
  }
}

provider "onepassword" {}

data "onepassword_vault" "network" {
  name = "Network"
}

ephemeral "onepassword_item" "netbox" {
  vault = data.onepassword_vault.network.uuid
  title = "NetBox API Token - Tofu"
}

provider "netbox" {
  server_url = ephemeral.onepassword_item.netbox.hostname
  api_token  = ephemeral.onepassword_item.netbox.credential
}
