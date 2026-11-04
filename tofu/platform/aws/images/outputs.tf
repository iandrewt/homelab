output "release_role" {
  description = "What images.yml assumes to sign a release"
  value       = aws_iam_role.release.arn
}

output "keys" {
  description = "Each signing key, by its alias"
  value       = { for name, alias in aws_kms_alias.image : alias.name => aws_kms_key.image[name].arn }
}
