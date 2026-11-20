terraform {
  required_version = "~> 1.13"

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
      version = "~> 6.0"
    }
  }

  backend "s3" {
    bucket               = "tofu-state-877127520873-ap-southeast-2-an"
    region               = "ap-southeast-2"
    profile              = "homelab-state-platform"
    workspace_key_prefix = "platform/proxmox/admin/cluster"
    key                  = "terraform.tfstate"
    use_lockfile         = true
  }

  encryption {
    key_provider "aws_kms" "state" {
      kms_key_id         = "arn:aws:kms:ap-southeast-2:877127520873:alias/tofu-state"
      key_spec           = "AES_256"
      region             = "ap-southeast-2"
      profile            = "homelab-state-platform"
      encryption_context = { state = "platform/proxmox/admin/cluster/${terraform.workspace}/terraform.tfstate" }
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

locals {
  cluster = yamldecode(file("${path.module}/../../clusters/${terraform.workspace}.yaml"))
}

provider "netbox" {
  server_url = ephemeral.onepassword_item.netbox.hostname
  api_token  = ephemeral.onepassword_item.netbox.credential
}

data "netbox_cluster" "this" {
  name = terraform.workspace
}

data "netbox_devices" "nodes" {
  filters = [{ name = "cluster_id", value = tostring(data.netbox_cluster.this.id) }]
}

data "netbox_device_interfaces" "nodes" {
  for_each = { for d in data.netbox_devices.nodes.devices : d.name => d.id }

  filters = [{ name = "device_id", value = tostring(each.value) }]
}

data "netbox_ip_addresses" "management" {
  filters = [{ name = "id", value = tostring(data.netbox_devices.nodes.devices[0].primary_ip6_id) }]
}

module "netbox" {
  source = "../../../../modules/netbox-site"

  site_slug  = local.cluster.site
  trust_tags = ["proxmox-api"]
  # The nodes' management network states who may manage them
  destinations = { management = split("/", one(data.netbox_ip_addresses.management.ip_addresses).ip_address)[0] }
}

provider "onepassword" {}

data "onepassword_vault" "this" {
  name = local.cluster.onepassword_vault
}

ephemeral "onepassword_item" "netbox" {
  vault = data.onepassword_vault.this.uuid
  title = "NetBox API Token - Tofu"
}

ephemeral "onepassword_item" "token" {
  vault = data.onepassword_vault.this.uuid
  title = "Proxmox API Token - ${terraform.workspace} cluster"
}

provider "proxmox" {
  endpoint  = local.cluster.endpoint
  insecure  = try(local.cluster.insecure, false)
  api_token = ephemeral.onepassword_item.token.password
}
