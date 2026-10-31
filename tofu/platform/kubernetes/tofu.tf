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
      version = "~> 5.8"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.10"
    }
    talos = {
      source  = "siderolabs/talos"
      version = "~> 0.12"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.1"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 3.0"
    }
    http = {
      source  = "hashicorp/http"
      version = "~> 3.5"
    }
    external = {
      source  = "hashicorp/external"
      version = "~> 2.3"
    }
  }

  # A workspace per cluster
  backend "s3" {
    bucket               = "tofu-state-877127520873-ap-southeast-2-an"
    region               = "ap-southeast-2"
    profile              = "homelab-state-platform"
    workspace_key_prefix = "platform/kubernetes"
    key                  = "terraform.tfstate"
    use_lockfile         = true
  }

  encryption {
    key_provider "aws_kms" "state" {
      kms_key_id         = "arn:aws:kms:ap-southeast-2:877127520873:alias/tofu-state"
      key_spec           = "AES_256"
      region             = "ap-southeast-2"
      profile            = "homelab-state-platform"
      encryption_context = { state = "platform/kubernetes/${terraform.workspace}/terraform.tfstate" }
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
  name    = terraform.workspace
  team    = "platform"
  cluster = yamldecode(file("${path.module}/clusters/${local.name}.yaml"))

  # A superset of where the VMs are: a provider has to outlive what it made
  proxmox = {
    for pve in local.cluster.proxmox :
    pve => yamldecode(file("${path.module}/../proxmox/clusters/${pve}.yaml"))
  }
}

module "tenancy" {
  source = "../../modules/tenancy"
}

provider "onepassword" {}

data "onepassword_vault" "team" {
  name = module.tenancy.teams[local.team].title
}

ephemeral "onepassword_item" "netbox" {
  vault = data.onepassword_vault.team.uuid
  title = "NetBox API Token - Tofu"
}

provider "netbox" {
  server_url = ephemeral.onepassword_item.netbox.hostname
  api_token  = ephemeral.onepassword_item.netbox.credential
}

# Reads only
provider "azuread" {}

ephemeral "onepassword_item" "proxmox" {
  for_each = local.proxmox

  vault = data.onepassword_vault.team.uuid
  title = "Proxmox API Token - ${each.key} tenant-${local.team}"
}

provider "proxmox" {
  alias    = "cluster"
  for_each = local.proxmox

  endpoint  = each.value.endpoint
  insecure  = try(each.value.insecure, false)
  api_token = ephemeral.onepassword_item.proxmox[each.key].password

  ssh {
    agent    = true
    username = each.value.ssh_username
  }
}

provider "talos" {}

provider "kubernetes" {
  host                   = module.talos.kubernetes.host
  cluster_ca_certificate = base64decode(module.talos.kubernetes.ca_certificate)
  client_certificate     = base64decode(module.talos.kubernetes.client_certificate)
  client_key             = base64decode(module.talos.kubernetes.client_key)
}

provider "helm" {
  kubernetes = {
    host                   = module.talos.kubernetes.host
    cluster_ca_certificate = base64decode(module.talos.kubernetes.ca_certificate)
    client_certificate     = base64decode(module.talos.kubernetes.client_certificate)
    client_key             = base64decode(module.talos.kubernetes.client_key)
  }
}
