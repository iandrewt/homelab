output "release_role" {
  description = "What images.yml assumes to sign a release, IMAGES_RELEASE_ROLE"
  value       = aws_iam_role.release.arn
}

output "key" {
  description = "The signing key, HOMELAB_IMAGES_KEY: its alias's ARN, which a principal in another account names it by"
  value       = aws_kms_alias.signing.arn
}
