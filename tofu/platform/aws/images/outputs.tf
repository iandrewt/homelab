output "release_role" {
  description = "What images.yml assumes to sign a release"
  value       = aws_iam_role.release.arn
}

output "key" {
  description = "The signing key"
  value       = aws_kms_alias.signing.arn
}

output "bucket" {
  description = "Where a release publishes its updates"
  value       = aws_s3_bucket.updates.id
}

output "update_url" {
  description = "What sysupdate reads, IMAGES_UPDATE_URL: <url>/<image>/<channel>/"
  value       = "https://${aws_cloudfront_distribution.updates.domain_name}"
}
