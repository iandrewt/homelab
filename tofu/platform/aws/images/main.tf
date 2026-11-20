data "aws_caller_identity" "current" {}

locals {
  # As the repository's immutable subject claim names it, the owner and repository by ID too, so a renamed
  # or recreated one can't take the role
  repository = "iandrewt@12795256/homelab@1394808425"
  # What UEFI, the kernel's verity check, systemd-measure and OpenPGP all verify
  signing_algorithm = "RSASSA_PKCS1_V1_5_SHA_256"
}

data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

resource "aws_kms_key" "signing" {
  description              = "Signs Platform's UKIs, the verity of its images and extensions, the PCR 11 values they expect, and their update manifests"
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

resource "aws_kms_alias" "signing" {
  name          = "alias/images"
  target_key_id = aws_kms_key.signing.key_id
}

# Only a job in the release environment, which takes an image/* or extension/* tag and its reviewer's approval
resource "aws_iam_role" "release" {
  name = "images-release"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:${local.repository}:environment:release"
        }
      }
    }]
  })
}
