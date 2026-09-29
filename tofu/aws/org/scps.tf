locals {
  bootstrap_role = "arn:aws:iam::*:role/OrganizationAccountAccessRole"

  # Global services, which alone may use us-east-1. One missing here fails with an explicit
  # SCP deny, and joins the list then
  global_actions = [
    "account:*",
    "aws-portal:*",
    "billing:*",
    "budgets:*",
    "ce:*",
    "cloudtrail:LookupEvents",
    "cur:*",
    "freetier:*",
    "health:*",
    "iam:*",
    "invoicing:*",
    "organizations:*",
    "payments:*",
    "pricing:*",
    "route53:*",
    "route53domains:*",
    "s3:GetBucketLocation",
    "s3:ListAllMyBuckets",
    "sts:*",
    "support:*",
    "tax:*",
    "trustedadvisor:*",
  ]

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
    Statement = [
      {
        Sid       = "OnlyTheseRegions"
        Effect    = "Deny"
        NotAction = local.global_actions
        Resource  = "*"
        Condition = {
          StringNotEquals = { "aws:RequestedRegion" = local.regions }
          ArnNotLike      = { "aws:PrincipalArn" = local.bootstrap_role }
        }
      },
      {
        Sid      = "GlobalServicesFromUsEast1"
        Effect   = "Deny"
        Action   = local.global_actions
        Resource = "*"
        Condition = {
          StringNotEquals = { "aws:RequestedRegion" = concat(local.regions, ["us-east-1"]) }
          ArnNotLike      = { "aws:PrincipalArn" = local.bootstrap_role }
        }
      },
    ]
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
