output "token_id" {
  description = "Full token id, user@realm!name."
  value       = local.token_id
}

output "item_title" {
  description = "1Password item the secret was written to."
  value       = onepassword_item.token.title
}
