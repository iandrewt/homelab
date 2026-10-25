locals {
  fetch_node = sort(keys(local.cluster.nodes))[0]
}

resource "proxmox_download_file" "this" {
  for_each = local.library.images

  node_name    = local.fetch_node
  datastore_id = local.cluster.image_storage
  content_type = each.value.content_type
  url          = each.value.url
  file_name    = each.value.file_name

  checksum           = each.value.checksum
  checksum_algorithm = each.value.checksum_algorithm
}

output "volume_ids" {
  description = "Volume id of each downloaded image."
  value       = { for k, v in proxmox_download_file.this : k => v.id }
}
