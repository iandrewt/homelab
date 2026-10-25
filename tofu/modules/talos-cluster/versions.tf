terraform {
  required_providers {
    talos = {
      source = "siderolabs/talos"
    }
    helm = {
      source = "hashicorp/helm"
    }
    onepassword = {
      source = "1Password/onepassword"
    }
  }
}
