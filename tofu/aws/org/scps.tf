locals {
  bootstrap_role = "arn:aws:iam::*:role/OrganizationAccountAccessRole"

  # Allowed in every region: services with no regional resources of their own, and allow
  # inspecting resources in denied regions
  anywhere = [
    "access-analyzer:*",
    "account:*",
    "artifact:*",
    "autoscaling:Describe*",
    "aws-marketplace-management:*",
    "aws-marketplace:*",
    "aws-portal:*",
    "bcm-dashboards:*",
    "bcm-data-exports:*",
    "bcm-pricing-calculator:*",
    "bcm-recommended-actions:*",
    "billing:*",
    "billingconductor:*",
    "budgets:*",
    "ce:*",
    "chatbot:*",
    "cloudfront-keyvaluestore:*",
    "cloudfront:*",
    "cloudtrail:Describe*",
    "cloudtrail:Get*",
    "cloudtrail:List*",
    "cloudtrail:LookupEvents",
    "compute-optimizer:*",
    "consoleapp:*",
    "consolidatedbilling:*",
    "cost-optimization-hub:*",
    "cur:*",
    "ec2:Describe*",
    "ecs:List*",
    "freetier:*",
    "globalaccelerator:*",
    "health:*",
    "iam:*",
    "invoicing:*",
    "networkmanager:*",
    "notifications-contacts:*",
    "notifications:*",
    "organizations:*",
    "outposts:List*",
    "payments:*",
    "pricing:*",
    "purchase-orders:*",
    "rds:Describe*",
    "route53-recovery-cluster:*",
    "route53-recovery-control-config:*",
    "route53-recovery-readiness:*",
    "route53:*",
    "route53domains:*",
    "s3:*AccountPublicAccessBlock",
    "s3:*MultiRegionAccessPoint*",
    "s3:*StorageLens*",
    "s3:GetBucketLocation",
    "s3:ListAllMyBuckets",
    "savingsplans:*",
    "security-ir:*",
    "shield:*",
    "signin:*",
    "sts:*",
    "support-console:*",
    "support:*",
    "supportapp:*",
    "supportplans:*",
    "sustainability:*",
    "tax:*",
    "trustedadvisor:*",
    "waf:*",
  ]

  # Regional services that global ones depend on, allowed in the one region they have to be in.
  pinned = {
    "us-east-1" = [
      "acm:*",
      "cloudwatch:*",
      "ecr-public:*",
      "events:*",
      "fms:*",
      "kms:*",
      "logs:*",
      "sns:*",
      "wafv2:*",
    ]
  }

  # Administrator where the logging and detection layers run
  security_administrators = [
    for account in ["Log Archive", "Security"] :
    "arn:aws:iam::${local.account_ids[account]}:role/aws-reserved/sso.amazonaws.com/*/AWSReservedSSO_Administrator_*"
  ]
}

resource "aws_organizations_policy" "guardrails" {
  name = "Guardrails"
  type = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "StayInTheOrganisation"
        Effect   = "Deny"
        Action   = "organizations:LeaveOrganization"
        Resource = "*"
      },
      {
        Sid      = "NoRootUser"
        Effect   = "Deny"
        Action   = "*"
        Resource = "*"
        Condition = {
          ArnLike = { "aws:PrincipalArn" = "arn:aws:iam::*:root" }
          Null    = { "aws:AssumedRoot" = "true" }
        }
      },
      {
        Sid      = "RegionsFromManagement"
        Effect   = "Deny"
        Action   = ["account:EnableRegion", "account:DisableRegion"]
        Resource = "*"
      },
      {
        Sid      = "NoDefaultVpcs"
        Effect   = "Deny"
        Action   = ["ec2:CreateDefaultVpc", "ec2:CreateDefaultSubnet"]
        Resource = "*"
      },
      {
        Sid    = "EntraOwnsIdentities"
        Effect = "Deny"
        Action = [
          "identitystore:Create*",
          "identitystore:Delete*",
          "identitystore:Update*",
          "sso-directory:CreateBearerToken",
        ]
        Resource = "*"
      },
      {
        Sid       = "RetentionIsBreakGlass"
        Effect    = "Deny"
        Action    = "s3:BypassGovernanceRetention"
        Resource  = "*"
        Condition = { ArnNotLike = { "aws:PrincipalArn" = local.bootstrap_role } }
      },
      {
        Sid    = "DetectionStaysOn"
        Effect = "Deny"
        Action = [
          "access-analyzer:CreateArchiveRule",
          "access-analyzer:DeleteAnalyzer",
          "access-analyzer:UpdateArchiveRule",
          "cloudtrail:DeleteTrail",
          "cloudtrail:PutEventSelectors",
          "cloudtrail:StopLogging",
          "cloudtrail:UpdateTrail",
          "guardduty:CreateFilter",
          "guardduty:DeleteDetector",
          "guardduty:DeleteMembers",
          "guardduty:DisassociateFromAdministratorAccount",
          "guardduty:DisassociateFromMasterAccount",
          "guardduty:DisassociateMembers",
          "guardduty:StopMonitoringMembers",
          "guardduty:UpdateDetector",
          "guardduty:UpdateFilter",
        ]
        Resource  = "*"
        Condition = { ArnNotLike = { "aws:PrincipalArn" = concat([local.bootstrap_role], local.security_administrators) } }
      },
    ]
  })
}

# baseline deletes every region's default VPC through the bootstrap role
resource "aws_organizations_policy" "regions" {
  name = "Regions"
  type = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [{
        Sid       = "OnlyTheseRegions"
        Effect    = "Deny"
        NotAction = concat(local.anywhere, flatten(values(local.pinned)))
        Resource  = "*"
        Condition = {
          StringNotEquals = { "aws:RequestedRegion" = local.regions }
          ArnNotLike      = { "aws:PrincipalArn" = local.bootstrap_role }
        }
      }],
      [for region, actions in local.pinned : {
        Sid      = "PinnedTo${replace(title(replace(region, "-", " ")), " ", "")}"
        Effect   = "Deny"
        Action   = actions
        Resource = "*"
        Condition = {
          StringNotEquals = { "aws:RequestedRegion" = concat(local.regions, [region]) }
          ArnNotLike      = { "aws:PrincipalArn" = local.bootstrap_role }
        }
      }],
    )
  })
}

resource "aws_organizations_policy" "bootstrap_role" {
  name = "Bootstrap role"
  type = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "BootstrapRoleIsFixed"
      Effect = "Deny"
      Action = [
        "iam:AttachRolePolicy",
        "iam:CreateRole",
        "iam:DeleteRole",
        "iam:DeleteRolePermissionsBoundary",
        "iam:DeleteRolePolicy",
        "iam:DetachRolePolicy",
        "iam:PutRolePermissionsBoundary",
        "iam:PutRolePolicy",
        "iam:UpdateAssumeRolePolicy",
        "iam:UpdateRole",
        "iam:UpdateRoleDescription",
      ]
      Resource = local.bootstrap_role
    }]
  })
}

resource "aws_organizations_policy" "suspended" {
  name = "Suspended"
  type = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Sid = "Nothing", Effect = "Deny", Action = "*", Resource = "*" }]
  })
}

resource "aws_organizations_policy_attachment" "root_scps" {
  for_each = {
    guardrails = aws_organizations_policy.guardrails.id
    regions    = aws_organizations_policy.regions.id
  }

  policy_id = each.value
  target_id = local.root_id
}

# On the OUs, not the root: a new account is created in the root, where Organizations makes
# the role, and moved after
resource "aws_organizations_policy_attachment" "bootstrap_role" {
  for_each = aws_organizations_organizational_unit.top

  policy_id = aws_organizations_policy.bootstrap_role.id
  target_id = each.value.id
}

resource "aws_organizations_policy_attachment" "suspended" {
  policy_id = aws_organizations_policy.suspended.id
  target_id = aws_organizations_organizational_unit.top["Suspended"].id
}
