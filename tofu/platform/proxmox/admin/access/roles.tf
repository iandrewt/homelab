resource "proxmox_virtual_environment_role" "tofu_cluster" {
  role_id = "TofuCluster"

  privileges = [
    "Sys.Modify", # cluster options, node firewall
    "Sys.Audit",
    "Datastore.Allocate", # storage definitions
    "Datastore.Audit",
    "Mapping.Modify", # hardware mappings
    "Mapping.Audit",
    "SDN.Allocate", # the VLAN zone and its VNets
  ]
}

resource "proxmox_virtual_environment_role" "tofu_images" {
  role_id = "TofuImages"

  privileges = [
    "Datastore.Allocate", # replacing an image deletes the volume it replaces
    "Datastore.AllocateTemplate",
    "Datastore.Audit",
  ]
}

resource "proxmox_virtual_environment_role" "tofu_network_fetch" {
  role_id = "TofuNetworkFetch"

  privileges = ["Sys.AccessNetwork"]
}
