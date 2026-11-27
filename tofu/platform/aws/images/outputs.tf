output "publish_role" {
  description = "What images.yml assumes to publish a release, IMAGES_PUBLISH_ROLE"
  value       = aws_iam_role.publish.arn
}

output "bucket" {
  description = "Where a release publishes its updates"
  value       = aws_s3_bucket.updates.id
}

output "update_url" {
  description = "What sysupdate reads, IMAGES_UPDATE_URL: <url>/<image>/<channel>/"
  value       = "https://${aws_cloudfront_distribution.updates.domain_name}"
}
