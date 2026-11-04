data "aws_caller_identity" "current" {}

locals {
  repository = "iandrewt/homelab"
  # What UEFI, the kernel's verity check and systemd-measure all verify
  signing_algorithm = "RSASSA_PKCS1_V1_5_SHA_256"

  keys = {
    secure-boot = "Signs Platform's UKIs, systemd-boot, and the verity of its images and extensions, as db trusts"
    pcr         = "Signs the PCR 11 values Platform's UKIs expect, which unlocking their disks is bound to"
  }
}

# baseline's, in every member account
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

resource "aws_kms_key" "image" {
  for_each = local.keys

  description              = each.value
  customer_master_key_spec = "RSA_2048"
  key_usage                = "SIGN_VERIFY"
  deletion_window_in_days  = 30

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
        Sid       = "ReleasesSign"
        Effect    = "Allow"
        Principal = { AWS = aws_iam_role.release.arn }
        Action    = "kms:Sign"
        Resource  = "*"
        Condition = {
          StringEquals = { "kms:SigningAlgorithm" = local.signing_algorithm, "kms:MessageType" = "DIGEST" }
        }
      },
      {
        Sid       = "ReleasesRead"
        Effect    = "Allow"
        Principal = { AWS = aws_iam_role.release.arn }
        Action    = ["kms:GetPublicKey", "kms:DescribeKey"]
        Resource  = "*"
      },
      # The first statement lets the account's IAM grant kms:Sign too, which this takes back
      {
        Sid       = "OnlyReleasesSign"
        Effect    = "Deny"
        Principal = "*"
        Action    = "kms:Sign"
        Resource  = "*"
        Condition = { ArnNotEquals = { "aws:PrincipalArn" = aws_iam_role.release.arn } }
      },
    ]
  })

  # A new key is a new db certificate in every machine's firmware
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_kms_alias" "image" {
  for_each = local.keys

  name          = "alias/images-${each.key}"
  target_key_id = aws_kms_key.image[each.key].key_id
}

# Only a release, which only someone who can push a tag makes
resource "aws_iam_role" "release" {
  name = "images-release"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com" }
        StringLike   = { "token.actions.githubusercontent.com:sub" = "repo:${local.repository}:ref:refs/tags/image/*" }
      }
    }]
  })
}
