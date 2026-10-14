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
  }

  backend "s3" {
    bucket       = "tofu-state-585242447535-ap-southeast-2-an"
    region       = "ap-southeast-2"
    profile      = "homelab-state-platform"
    key          = "platform/proxmox/alfheim/valhalla/terraform.tfstate"
    use_lockfile = true
  }

  encryption {
    key_provider "aws_kms" "state" {
      kms_key_id         = "arn:aws:kms:ap-southeast-2:585242447535:alias/tofu-state"
      key_spec           = "AES_256"
      region             = "ap-southeast-2"
      profile            = "homelab-state-platform"
      encryption_context = { state = "platform/proxmox/alfheim/valhalla/terraform.tfstate" }
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
  cluster_name = "alfheim"
  tenant       = "platform"

  cluster = yamldecode(file("${path.module}/../../clusters/${local.cluster_name}.yaml"))
}

provider "netbox" {
  server_url = ephemeral.onepassword_item.netbox.hostname
  api_token  = ephemeral.onepassword_item.netbox.credential
}

module "netbox" {
  source = "../../../../modules/netbox-site"

  site_slug    = local.cluster.site
  prefix_roles = ["pods", "lb-local", "lb-shared", "lb-external"]
}

provider "onepassword" {}

data "onepassword_vault" "tenant" {
  name = title(local.tenant)
}

ephemeral "onepassword_item" "netbox" {
  vault = data.onepassword_vault.tenant.uuid
  title = "NetBox API Token - Tofu"
}

ephemeral "onepassword_item" "token" {
  vault = data.onepassword_vault.tenant.uuid
  title = "Proxmox API Token - ${local.cluster_name} tenant-${local.tenant}"
}

provider "proxmox" {
  endpoint  = local.cluster.endpoint
  api_token = ephemeral.onepassword_item.token.password

  ssh {
    agent    = true
    username = local.cluster.ssh_username
  }
}

provider "talos" {}

# Minted from the machine secrets, so neither provider needs the cluster to exist at plan
ephemeral "talos_cluster_kubeconfig" "admin" {
  machine_secrets = local.machine_secrets
  cluster_name    = local.talos.cluster_name
  endpoint        = local.talos.endpoint
}

provider "kubernetes" {
  host                   = ephemeral.talos_cluster_kubeconfig.admin.kubernetes_client_configuration.host
  cluster_ca_certificate = base64decode(ephemeral.talos_cluster_kubeconfig.admin.kubernetes_client_configuration.ca_certificate)
  client_certificate     = base64decode(ephemeral.talos_cluster_kubeconfig.admin.kubernetes_client_configuration.client_certificate)
  client_key             = base64decode(ephemeral.talos_cluster_kubeconfig.admin.kubernetes_client_configuration.client_key)
}

provider "helm" {
  kubernetes = {
    host                   = ephemeral.talos_cluster_kubeconfig.admin.kubernetes_client_configuration.host
    cluster_ca_certificate = base64decode(ephemeral.talos_cluster_kubeconfig.admin.kubernetes_client_configuration.ca_certificate)
    client_certificate     = base64decode(ephemeral.talos_cluster_kubeconfig.admin.kubernetes_client_configuration.client_certificate)
    client_key             = base64decode(ephemeral.talos_cluster_kubeconfig.admin.kubernetes_client_configuration.client_key)
  }
}
