data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_organizations_organization" "this" {
  return_organization_only = true
}

locals {
  trail_name = "management-events"
  trail_arn  = "arn:aws:cloudtrail:${data.aws_region.current.region}:${data.aws_organizations_organization.this.master_account_id}:trail/${local.trail_name}"
}

resource "aws_kms_key" "trail" {
  description             = "CloudTrail Organization Logs"
  enable_key_rotation     = true
  deletion_window_in_days = 30

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "Enable IAM User Permissions"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "Allow CloudTrail to encrypt logs"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "kms:GenerateDataKey*"
        Resource  = "*"
        Condition = {
          StringEquals = { "aws:SourceArn" = local.trail_arn }
          StringLike = {
            "kms:EncryptionContext:aws:cloudtrail:arn" = "arn:aws:cloudtrail:*:${data.aws_organizations_organization.this.master_account_id}:trail/*"
          }
        }
      },
      {
        Sid       = "Allow CloudTrail to describe key"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "kms:DescribeKey"
        Resource  = "*"
      },
      {
        Sid       = "Allow principals in the account to decrypt log files"
        Effect    = "Allow"
        Principal = { AWS = "*" }
        Action    = ["kms:Decrypt", "kms:ReEncryptFrom"]
        Resource  = "*"
        Condition = {
          StringEquals = { "kms:CallerAccount" = data.aws_organizations_organization.this.master_account_id }
          StringLike = {
            "kms:EncryptionContext:aws:cloudtrail:arn" = "arn:aws:cloudtrail:*:${data.aws_organizations_organization.this.master_account_id}:trail/*"
          }
        }
      }
    ]
  })

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_kms_alias" "trail" {
  name          = "alias/cloudtrail"
  target_key_id = aws_kms_key.trail.key_id
}

resource "aws_s3_bucket" "trail" {
  bucket              = "cloudtrail-${data.aws_caller_identity.current.account_id}-${data.aws_region.current.region}-an"
  bucket_namespace    = "account-regional"
  object_lock_enabled = true

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "trail" {
  bucket = aws_s3_bucket.trail.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_object_lock_configuration" "trail" {
  bucket = aws_s3_bucket.trail.id

  rule {
    default_retention {
      mode = "GOVERNANCE"
      days = 90
    }
  }

  depends_on = [aws_s3_bucket_versioning.trail]
}

resource "aws_s3_bucket_lifecycle_configuration" "trail" {
  bucket = aws_s3_bucket.trail.id

  rule {
    id     = "delete-after-year"
    status = "Enabled"

    filter {}

    expiration {
      days = 365
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.trail]
}

resource "aws_s3_bucket_policy" "trail" {
  bucket = aws_s3_bucket.trail.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AWSCloudTrailAclCheck"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:GetBucketAcl"
        Resource  = aws_s3_bucket.trail.arn
        Condition = { StringEquals = { "aws:SourceArn" = local.trail_arn } }
      },
      {
        Sid       = "AWSCloudTrailWrite"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource = [
          "${aws_s3_bucket.trail.arn}/AWSLogs/${data.aws_organizations_organization.this.master_account_id}/*",
          "${aws_s3_bucket.trail.arn}/AWSLogs/${data.aws_organizations_organization.this.id}/*",
        ]
        Condition = {
          StringEquals = {
            "s3:x-amz-acl"  = "bucket-owner-full-control"
            "aws:SourceArn" = local.trail_arn
          }
        }
      },
    ]
  })
}

# What detection mails guardrail events from, every account's and region's in one place. The
# bucket is the archive; this keeps a fortnight for looking into one.
resource "aws_cloudwatch_log_group" "trail" {
  provider = aws.security

  name              = "/aws/cloudtrail/${local.trail_name}"
  retention_in_days = 14
}

resource "aws_iam_role" "trail_logs" {
  provider = aws.security

  name = "cloudtrail-logs"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "cloudtrail.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceArn" = local.trail_arn } }
    }]
  })
}

resource "aws_iam_role_policy" "trail_logs" {
  provider = aws.security

  role = aws_iam_role.trail_logs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.trail.arn}:log-stream:*"
    }]
  })
}

resource "aws_cloudtrail" "organisation" {
  provider = aws.security

  name                          = local.trail_name
  s3_bucket_name                = aws_s3_bucket.trail.id
  kms_key_id                    = aws_kms_key.trail.arn
  is_organization_trail         = true
  is_multi_region_trail         = true
  include_global_service_events = true
  enable_log_file_validation    = true

  # A delegated administrator's trail delivers to its own account's group and role
  cloud_watch_logs_group_arn = "${aws_cloudwatch_log_group.trail.arn}:*"
  cloud_watch_logs_role_arn  = aws_iam_role.trail_logs.arn

  depends_on = [aws_s3_bucket_policy.trail, aws_s3_bucket_object_lock_configuration.trail, aws_iam_role_policy.trail_logs]
}
