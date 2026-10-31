terraform {
  required_version = "~> 1.13"

  required_providers {
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.10"
    }
  }

  backend "s3" {
    bucket       = "tofu-state-877127520873-ap-southeast-2-an"
    region       = "ap-southeast-2"
    profile      = "homelab-state-platform"
    key          = "platform/entra/apps/terraform.tfstate"
    use_lockfile = true
  }

  encryption {
    key_provider "aws_kms" "state" {
      kms_key_id         = "arn:aws:kms:ap-southeast-2:877127520873:alias/tofu-state"
      key_spec           = "AES_256"
      region             = "ap-southeast-2"
      profile            = "homelab-state-platform"
      encryption_context = { state = "platform/entra/apps/terraform.tfstate" }
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

# An admin's `az login`, never unattended: assignments need AppRoleAssignment.ReadWrite.All
provider "azuread" {}

module "tenancy" {
  source = "../../../modules/tenancy"
}
