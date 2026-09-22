# TODO: bump the constraint once proxmox_network_applier and the interface `reload` flag
# ship - they're on bpg/terraform-provider-proxmox#3063 and not in any release yet, so
# `tofu init` from the registry can't validate this module until then.
terraform {
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.114"
    }
  }
}
