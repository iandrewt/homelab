terraform {
  required_version = "~> 1.13"

  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.13"
    }
    onepassword = {
      source  = "1Password/onepassword"
      version = "~> 3.3"
    }
  }

  backend "s3" {
    bucket       = "tofu-state-877127520873-ap-southeast-2-an"
    region       = "ap-southeast-2"
    profile      = "homelab-state-security"
    key          = "security/github/terraform.tfstate"
    use_lockfile = true
  }

  encryption {
    key_provider "aws_kms" "state" {
      kms_key_id         = "arn:aws:kms:ap-southeast-2:877127520873:alias/tofu-state"
      key_spec           = "AES_256"
      region             = "ap-southeast-2"
      profile            = "homelab-state-security"
      encryption_context = { state = "security/github/terraform.tfstate" }
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

# By hand alone, like the seed: these are the gates CI runs behind, so nothing CI holds may change
# them. The token administers this repository and no other, and is read only when this layer runs
provider "onepassword" {}

data "onepassword_vault" "break_glass" {
  name = "Break-glass"
}

ephemeral "onepassword_item" "github" {
  vault = data.onepassword_vault.break_glass.uuid
  title = "GitHub Token - Homelab Administration"
}

provider "github" {
  owner = "iandrewt"
  token = ephemeral.onepassword_item.github.credential
}
