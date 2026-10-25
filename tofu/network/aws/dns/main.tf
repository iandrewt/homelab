data "aws_caller_identity" "current" {}

resource "aws_route53_zone" "titmuss_au" {
  name = "titmuss.au"

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_route53domains_registered_domain" "titmuss_au" {
  domain_name = aws_route53_zone.titmuss_au.name
  auto_renew  = true

  transfer_lock      = false
  registrant_privacy = false
  admin_privacy      = true
  tech_privacy       = true
  billing_privacy    = true

  dynamic "name_server" {
    for_each = aws_route53_zone.titmuss_au.name_servers

    content {
      name = name_server.value
    }
  }
}

resource "aws_kms_key" "dnssec" {
  region                   = "us-east-1"
  description              = "Route53's key-signing keys"
  customer_master_key_spec = "ECC_NIST_P256"
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
        Sid       = "Allow Route 53 DNSSEC Service"
        Effect    = "Allow"
        Principal = { Service = "dnssec-route53.amazonaws.com" }
        Action    = ["kms:DescribeKey", "kms:GetPublicKey", "kms:Sign"]
        Resource  = "*"
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
          ArnLike      = { "aws:SourceArn" = "arn:aws:route53:::hostedzone/*" }
        }
      },
      {
        Sid       = "Allow Route 53 DNSSEC to CreateGrant"
        Effect    = "Allow"
        Principal = { Service = "dnssec-route53.amazonaws.com" }
        Action    = "kms:CreateGrant"
        Resource  = "*"
        Condition = { Bool = { "kms:GrantIsForAWSResource" = "true" } }
      },
    ]
  })

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_kms_alias" "dnssec" {
  region        = "us-east-1"
  name          = "alias/dnssec"
  target_key_id = aws_kms_key.dnssec.key_id
}

locals {
  zones = { titmuss_au = aws_route53_zone.titmuss_au }
}

resource "aws_route53_key_signing_key" "this" {
  for_each = local.zones

  hosted_zone_id             = each.value.zone_id
  key_management_service_arn = aws_kms_key.dnssec.arn
  name                       = each.key
}

resource "aws_route53_hosted_zone_dnssec" "this" {
  for_each = local.zones

  hosted_zone_id = aws_route53_key_signing_key.this[each.key].hosted_zone_id
}
