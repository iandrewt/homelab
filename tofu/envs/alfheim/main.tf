locals {
  proxmox_node = "yggdrasil"
}

resource "proxmox_storage_zfspool" "data" {
  id       = "data"
  nodes    = [local.proxmox_node]
  zfs_pool = "data"
  content  = ["images", "rootdir"]

  thin_provision = true
}

resource "proxmox_download_file" "debian_13_genericcloud" {
  content_type = "import"
  datastore_id = "local"
  node_name    = local.proxmox_node
  url          = "https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2"
  file_name    = "debian-13-genericcloud-amd64.qcow2"
}

module "hugin" {
  source = "../../modules/vm"

  node_name   = local.proxmox_node
  vm_id       = 200
  hostname    = "hugin"
  description = "DNS resolver (primary)."

  image_file_id = proxmox_download_file.debian_13_genericcloud.id

  disk_datastore_id = proxmox_storage_zfspool.data.id

  cpu_cores    = 2
  memory_mb    = 1024
  disk_size_gb = 8

  vlan_id   = 20
  ipv4_cidr = "dhcp"
  ipv6_cidr = "auto"

  ci_username     = "debian"
  ssh_public_keys = [data.onepassword_item.control_ssh_key.public_key]
  tags            = ["dns-resolver"]
}
