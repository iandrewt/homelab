locals {
  zone = regex("^https://[^./]+\\.([^:/]+)", local.cluster.endpoint)[0]
}

resource "proxmox_acme_account" "default" {
  name      = "default"
  contact   = local.cluster.mailto
  directory = "https://acme-v02.api.letsencrypt.org/directory"
  tos       = "https://letsencrypt.org/documents/LE-SA-v1.8-July-06-2026.pdf"
}

data "aws_route53_zone" "public_copy" {
  name         = local.zone
  private_zone = false
}

# TODO: delete once internal CA is set up
resource "aws_iam_user" "acme" {
  name = "proxmox-acme-${terraform.workspace}"
}

resource "aws_iam_user_policy" "acme" {
  name = "acme-challenges"
  user = aws_iam_user.acme.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListZones"
        Effect   = "Allow"
        Action   = "route53:ListHostedZones"
        Resource = "*"
      },
      {
        Sid      = "ReadZone"
        Effect   = "Allow"
        Action   = "route53:ListResourceRecordSets"
        Resource = data.aws_route53_zone.public_copy.arn
      },
      {
        Sid      = "WriteZone"
        Effect   = "Allow"
        Action   = "route53:ChangeResourceRecordSets"
        Resource = data.aws_route53_zone.public_copy.arn
        Condition = {
          "ForAllValues:StringEquals" = { "route53:ChangeResourceRecordSetsRecordTypes" = ["TXT"] }
          "ForAllValues:StringLike"   = { "route53:ChangeResourceRecordSetsNormalizedRecordNames" = ["_acme-challenge.*"] }
        }
      },
    ]
  })
}

resource "aws_iam_access_key" "acme" {
  user = aws_iam_user.acme.name
}

resource "proxmox_acme_dns_plugin" "route53" {
  plugin = "route53"
  api    = "aws"

  # dns_aws returns before Route53 has synced the change
  validation_delay = 60

  data = {
    AWS_ACCESS_KEY_ID     = aws_iam_access_key.acme.id
    AWS_SECRET_ACCESS_KEY = aws_iam_access_key.acme.secret
  }
}

resource "proxmox_acme_certificate" "node" {
  for_each = local.cluster.nodes

  node_name = each.key
  account   = proxmox_acme_account.default.name

  domains = [{
    domain = "${each.key}.${local.zone}"
    plugin = proxmox_acme_dns_plugin.route53.plugin
  }]
}
