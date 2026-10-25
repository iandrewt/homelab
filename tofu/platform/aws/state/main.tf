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

module "tenancy" {
  source = "../../../modules/tenancy"
}

locals {
  teams = toset(keys(module.tenancy.teams))
}

# One role per team, and one policy for them all: the role's own `team` tag is what scopes
# it. Who may become the team is the trust policy's - today a member, by the tag Entra
# gives their session; CI by its OIDC token, when there is CI. The trust grants no
# sts:TagSession, so nobody can bring a `team` tag of their own.
resource "aws_iam_role" "state" {
  for_each = local.teams

  name = "tofu-state-${each.key}"
  path = "/tofu-state/"
  tags = { team = each.key }

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "Members"
      Effect    = "Allow"
      Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:PrincipalTag/team-${each.key}" = "true" } }
    }]
  })
}

resource "aws_iam_policy" "state" {
  name = "tofu-state"
  path = "/tofu-state/"

  # A layer's state lives under its path in tofu/, which starts with its team, and its
  # encryption context is that same key
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Without it a state not yet written is a 403, not a 404
        Sid      = "List"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.state.arn
      },
      {
        Sid      = "States"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.state.arn}/$${aws:PrincipalTag/team}/*"
      },
      {
        Sid       = "Keys"
        Effect    = "Allow"
        Action    = ["kms:GenerateDataKey", "kms:Decrypt"]
        Resource  = aws_kms_key.state.arn
        Condition = { StringLike = { "kms:EncryptionContext:state" = "$${aws:PrincipalTag/team}/*" } }
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "state" {
  for_each = local.teams

  role       = aws_iam_role.state[each.key].name
  policy_arn = aws_iam_policy.state.arn
}
