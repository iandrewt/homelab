data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

resource "aws_s3_bucket" "state" {
  bucket           = "tofu-state-${data.aws_caller_identity.current.account_id}-${data.aws_region.current.region}-an"
  bucket_namespace = "account-regional"

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "old-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    expiration {
      expired_object_delete_marker = true
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.state]
}

# Encrypts every layer's state and plans. The key policy is the default - the account
# delegates to IAM - so what each team may decrypt is TofuState's, by encryption context.
resource "aws_kms_key" "state" {
  description             = "OpenTofu state and plan encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 30

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_kms_alias" "state" {
  name          = "alias/tofu-state"
  target_key_id = aws_kms_key.state.key_id
}
