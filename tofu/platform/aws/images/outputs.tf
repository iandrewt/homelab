output "release_role" {
  description = "What images.yml assumes to sign a release"
  value       = aws_iam_role.release.arn
}

output "key" {
  description = "The signing key"
  value       = aws_kms_alias.signing.arn
}
