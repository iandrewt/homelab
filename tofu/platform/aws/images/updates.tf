# What sysupdate reads: CloudFront in front of a private bucket. Security's perimeter lets nothing read S3
# anonymously, and CloudFront reads it as a service acting for an account of the organisation

data "netbox_rirs" "all" {}

data "netbox_aggregates" "all" {}

data "netbox_sites" "all" {}

data "netbox_prefixes" "site" {
  for_each = { for s in data.netbox_sites.all.sites : s.slug => s.id }
  filters  = [{ name = "site_id", value = tostring(each.value) }]
}

locals {
  public_rirs = [for r in data.netbox_rirs.all.rirs : r.id if !r.is_private]
  # Every site's public addresses: the aggregates of a public RIR holding a prefix scoped to it
  sources = [
    for a in data.netbox_aggregates.all.aggregates : a
    if contains(local.public_rirs, a.rir_id) && anytrue(flatten([
      for d in values(data.netbox_prefixes.site) : [for p in d.prefixes : p.family == a.family && cidrcontains(a.prefix, p.prefix)]
    ]))
  ]
}

resource "aws_s3_bucket" "updates" {
  bucket           = "images-${data.aws_caller_identity.current.account_id}-ap-southeast-2-an"
  bucket_namespace = "account-regional"
}

resource "aws_s3_bucket_public_access_block" "updates" {
  bucket = aws_s3_bucket.updates.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "updates" {
  bucket = aws_s3_bucket.updates.id

  versioning_configuration {
    status = "Enabled"
  }
}

# A release prunes all but five versions a channel, and overwrites the sums
resource "aws_s3_bucket_lifecycle_configuration" "updates" {
  bucket = aws_s3_bucket.updates.id

  rule {
    id     = "expire-replaced"
    status = "Enabled"

    filter {}

    expiration {
      expired_object_delete_marker = true
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.updates]
}

resource "aws_s3_bucket_policy" "updates" {
  bucket = aws_s3_bucket.updates.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "CloudFrontReads"
        Effect    = "Allow"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.updates.arn}/*"
        Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.updates.arn } }
      },
      # systemd-pull tries each signature's name in turn, and only a 404 moves it on to the next
      {
        Sid       = "CloudFrontFindsNothing"
        Effect    = "Allow"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Action    = "s3:ListBucket"
        Resource  = aws_s3_bucket.updates.arn
        Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.updates.arn } }
      },
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.updates]
}

# A release's, which platform/aws/signing's role signs: only a job in a release or promote environment
resource "aws_iam_role" "publish" {
  name = "images-publish"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = [
            for e in ["image-release", "image-promote", "extension-release", "extension-promote"] :
            "repo:${local.repository}:environment:${e}"
          ]
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "publishes" {
  role = aws_iam_role.publish.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.updates.arn}/*"
      },
      # So a channel's missing sums are a 404, not a 403
      {
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.updates.arn
      },
    ]
  })
}

resource "aws_cloudfront_origin_access_control" "updates" {
  name                              = "images-updates"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

resource "aws_cloudfront_distribution" "updates" {
  enabled         = true
  comment         = "Platform's image updates"
  is_ipv6_enabled = true
  http_version    = "http2and3"
  web_acl_id      = aws_wafv2_web_acl.updates.arn

  origin {
    origin_id                = "updates"
    domain_name              = aws_s3_bucket.updates.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.updates.id
  }

  # A version's files never change
  default_cache_behavior {
    target_origin_id       = "updates"
    viewer_protocol_policy = "https-only"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.optimized.id
  }

  # A channel's sums do, at every release and promotion
  ordered_cache_behavior {
    path_pattern           = "*/SHA256SUMS*"
    target_origin_id       = "updates"
    viewer_protocol_policy = "https-only"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.disabled.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

resource "aws_wafv2_ip_set" "sites" {
  for_each = { IPV4 = 4, IPV6 = 6 }
  # CloudFront's, wherever the rest is
  region = "us-east-1"

  name               = "images-sites-${lower(each.key)}"
  scope              = "CLOUDFRONT"
  ip_address_version = each.key
  addresses          = sort([for a in local.sources : a.prefix if a.family == each.value])
}

# The plan requires one; it admits the sites alone
resource "aws_wafv2_web_acl" "updates" {
  region = "us-east-1"

  name  = "images-updates"
  scope = "CLOUDFRONT"

  default_action {
    block {}
  }

  dynamic "rule" {
    for_each = aws_wafv2_ip_set.sites
    content {
      name     = "sites-${lower(rule.key)}"
      priority = rule.value.ip_address_version == "IPV4" ? 0 : 1

      action {
        allow {}
      }

      statement {
        ip_set_reference_statement {
          arn = rule.value.arn
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "sites-${lower(rule.key)}"
        sampled_requests_enabled   = true
      }
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "images-updates"
    sampled_requests_enabled   = true
  }
}

resource "aws_pricingplanmanager_subscription" "updates" {
  plan_family   = "CloudFront"
  plan_tier     = "FREE"
  resource_arns = [aws_cloudfront_distribution.updates.arn, aws_wafv2_web_acl.updates.arn]
}
