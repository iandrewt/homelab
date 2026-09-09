output "token_items" {
  description = "1Password items for each layer."
  value = merge(
    { cluster = module.cluster_token.item_title },
    { images = module.images_token.item_title },
    { for k, m in module.identity_token : k => m.item_title },
  )
}
