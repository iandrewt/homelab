terraform {
  required_version = ">= 1.12.0"

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.114"
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

  # TODO: R2 or S3 once the bucket exists
  backend "local" {}

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

locals {
  cluster = yamldecode(file("${path.module}/../../clusters/${terraform.workspace}.yaml"))
}

provider "netbox" {
  server_url = ephemeral.onepassword_item.netbox.hostname
  api_token  = ephemeral.onepassword_item.netbox.credential
}

module "netbox" {
  source = "../../modules/netbox-site"

  site_slug        = local.cluster.site
  nodes            = keys(try(local.cluster.nodes, {}))
  bridge           = local.cluster.bridge
  management_vlans = try(local.cluster.firewall.management_vlans, [])
}

provider "onepassword" {}

data "onepassword_vault" "this" {
  name = local.cluster.onepassword_vault
}

ephemeral "onepassword_item" "netbox" {
  vault = data.onepassword_vault.this.uuid
  title = "Tofu NetBox API Token"
}

ephemeral "onepassword_item" "token" {
  vault = data.onepassword_vault.this.uuid
  title = "${local.cluster.onepassword_item_prefix} cluster token"
}

provider "proxmox" {
  endpoint  = local.cluster.endpoint
  api_token = ephemeral.onepassword_item.token.password
  insecure  = true # self-signed until there's an ACME account

  ssh {
    agent    = true
    username = local.cluster.ssh_username
  }
}
