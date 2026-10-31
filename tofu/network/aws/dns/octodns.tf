# What octoDNS writes Route53 as: NetBox's `public` view, never a cluster's own names
data "netbox_cluster_type" "kubernetes" {
  name = "Kubernetes"
}

data "netbox_clusters" "all" {}

locals {
  # Each writes `*.<cluster>.titmuss.au` with a role of its own
  clusters = sort([
    for c in data.netbox_clusters.all.clusters : c.name
    if c.cluster_type_id == data.netbox_cluster_type.kubernetes.cluster_type_id
  ])
}

data "aws_iam_policy_document" "octodns_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }

    # A role's own tags can't satisfy this, as they could a tag condition
    condition {
      test     = "ArnLike"
      variable = "aws:PrincipalArn"
      values   = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/aws-reserved/sso.amazonaws.com/*/AWSReservedSSO_Administrator_*"]
    }
  }
}

data "aws_iam_policy_document" "octodns" {
  statement {
    sid = "Read"
    actions = [
      "route53:GetChange",
      "route53:GetHostedZone",
      "route53:ListHealthChecks",
      "route53:ListHostedZones",
      "route53:ListHostedZonesByName",
      "route53:ListResourceRecordSets",
    ]
    resources = ["*"]
  }

  # A public reverse zone is NetBox's alone, so octoDNS makes its hosted zone on its first apply
  statement {
    sid       = "Write"
    actions   = ["route53:ChangeResourceRecordSets", "route53:CreateHostedZone"]
    resources = ["*"]
  }

  dynamic "statement" {
    for_each = length(local.clusters) > 0 ? [1] : []

    content {
      sid       = "NotClusters"
      effect    = "Deny"
      actions   = ["route53:ChangeResourceRecordSets"]
      resources = [aws_route53_zone.titmuss_au.arn]

      condition {
        test     = "ForAnyValue:StringLike"
        variable = "route53:ChangeResourceRecordSetsNormalizedRecordNames"
        values   = flatten([for c in local.clusters : ["${c}.${aws_route53_zone.titmuss_au.name}", "*.${c}.${aws_route53_zone.titmuss_au.name}"]])
      }
    }
  }
}

resource "aws_iam_role" "octodns" {
  name               = "octodns"
  description        = "octoDNS, writing NetBox's public view"
  assume_role_policy = data.aws_iam_policy_document.octodns_trust.json
}

resource "aws_iam_role_policy" "octodns" {
  role   = aws_iam_role.octodns.id
  name   = "route53"
  policy = data.aws_iam_policy_document.octodns.json
}
