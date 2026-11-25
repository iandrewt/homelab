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
    "pricingplanmanager:*",
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

  # Reading, reaching a team's state role, and reaching the account's tofu-plan
  deployment_sets = ["ReadOnly", "SecurityAudit", "TofuState"]

  # Administrator where the logging and detection layers run
  security_administrators = [
    for account in ["log-archive", "security-tooling"] :
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
          "sso:CreateInstanceAccessControlAttributeConfiguration",
          "sso:DeleteInstanceAccessControlAttributeConfiguration",
          "sso:UpdateInstanceAccessControlAttributeConfiguration",
        ]
        Resource = "*"
      },
      {
        Sid      = "NoLongLivedCredentials"
        Effect   = "Deny"
        Action   = ["iam:CreateAccessKey", "iam:CreateLoginProfile", "iam:CreateUser"]
        Resource = "*"
      },
      {
        # .au has no transfer lock
        Sid    = "DomainsStay"
        Effect = "Deny"
        Action = [
          "route53domains:DisableDomainAutoRenew",
          "route53domains:RetrieveDomainAuthCode",
          "route53domains:TransferDomainToAnotherAwsAccount",
        ]
        Resource  = "*"
        Condition = { ArnNotLike = { "aws:PrincipalArn" = local.bootstrap_role } }
      },
      {
        Sid       = "RetentionIsBreakGlass"
        Effect    = "Deny"
        Action    = "s3:BypassGovernanceRetention"
        Resource  = "*"
        Condition = { ArnNotLike = { "aws:PrincipalArn" = local.bootstrap_role } }
      },
      {
        # CI never needs either, even activated; a person's session carries no SubjectType
        Sid      = "NeverWorkloads"
        Effect   = "Deny"
        Action   = ["s3:BypassGovernanceRetention", "route53domains:RetrieveDomainAuthCode", "route53domains:TransferDomainToAnotherAwsAccount"]
        Resource = "*"
        Condition = {
          StringEquals = { "aws:OriginIdentity/SubjectType" = "workload" }
        }
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

locals {
  # Every identity provider's issuer, as IAM's issuer keys give it: OIDC's host and path
  identity_provider_issuers = ["token.actions.githubusercontent.com"]

  identity_provider_actions = [
    "iam:CreateOpenIDConnectProvider",
    "iam:CreateSAMLProvider",
    "iam:UpdateOpenIDConnectProvider",
    "iam:UpdateSAMLProvider",
  ]
}

# platform/aws/baseline makes them, through bootstrap; a role's trust in one is its own. The issuer
# statements are AWS's own, for trusted identity propagation, and bind bootstrap too
resource "aws_organizations_policy" "identity_providers" {
  name = "Identity providers"
  type = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "OnlyBootstrap"
        Effect = "Deny"
        Action = concat(local.identity_provider_actions, [
          "iam:AddClientIDToOpenIDConnectProvider",
          "iam:DeleteOpenIDConnectProvider",
          "iam:DeleteSAMLProvider",
          "iam:RemoveClientIDFromOpenIDConnectProvider",
          "iam:TagOpenIDConnectProvider",
          "iam:TagSAMLProvider",
          "iam:UntagOpenIDConnectProvider",
          "iam:UntagSAMLProvider",
          "iam:UpdateOpenIDConnectProviderThumbprint",
        ])
        Resource  = "*"
        Condition = { ArnNotLike = { "aws:PrincipalArn" = local.bootstrap_role } }
      },
      {
        Sid      = "OnlyApprovedIssuers"
        Effect   = "Deny"
        Action   = ["iam:CreateOpenIDConnectProvider", "iam:CreateSAMLProvider", "iam:UpdateSAMLProvider"]
        Resource = "*"
        Condition = {
          Null                          = { "iam:RequestIdentityProviderIssuers" = "false" }
          "ForAnyValue:StringNotEquals" = { "iam:RequestIdentityProviderIssuers" = local.identity_provider_issuers }
        }
      },
      {
        Sid      = "PropagationOnlyFromApprovedIssuers"
        Effect   = "Deny"
        Action   = local.identity_provider_actions
        Resource = "*"
        Condition = {
          Bool                          = { "iam:EnableIdentityPropagation" = "true" }
          Null                          = { "iam:ResourceIdentityProviderIssuers" = "false" }
          "ForAnyValue:StringNotEquals" = { "iam:ResourceIdentityProviderIssuers" = local.identity_provider_issuers }
        }
      },
      {
        Sid      = "PropagationOnlyFromKnownIssuers"
        Effect   = "Deny"
        Action   = local.identity_provider_actions
        Resource = "*"
        Condition = {
          Bool = { "iam:EnableIdentityPropagation" = "true" }
          Null = { "iam:ResourceIdentityProviderIssuers" = "true" }
        }
      },
    ]
  })
}

# baseline deletes every region's default VPC through the bootstrap role, and plans it as tofu-plan,
# which reads alone, and only bootstrap can make
locals {
  everywhere = [local.bootstrap_role, "arn:aws:iam::*:role/org/tofu-plan"]
}

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
          ArnNotLike      = { "aws:PrincipalArn" = local.everywhere }
        }
      }],
      [for region, actions in local.pinned : {
        Sid      = "PinnedTo${replace(title(replace(region, "-", " ")), " ", "")}"
        Effect   = "Deny"
        Action   = actions
        Resource = "*"
        Condition = {
          StringNotEquals = { "aws:RequestedRegion" = concat(local.regions, [region]) }
          ArnNotLike      = { "aws:PrincipalArn" = local.everywhere }
        }
      }],
    )
  })
}

locals {
  role_changes = [
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
}

# Bootstrap's role, to everyone; the organisation's own roles, under /org/, to all but bootstrap.
# A team's other roles are its own
resource "aws_organizations_policy" "bootstrap_role" {
  name = "Fixed roles"
  type = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "BootstrapRoleIsFixed"
        Effect   = "Deny"
        Action   = local.role_changes
        Resource = local.bootstrap_role
      },
      {
        Sid       = "OrgRolesAreFixed"
        Effect    = "Deny"
        Action    = local.role_changes
        Resource  = "arn:aws:iam::*:role/org/*"
        Condition = { ArnNotLike = { "aws:PrincipalArn" = local.bootstrap_role } }
      },
    ]
  })
}

# Release machinery is administered by bootstrap alone. Every role in such an account was made by
# something already trusted, so the one way in from outside is Identity Center: whoever
# administers it could assign themselves anything here, so only its read-only sets are let in
resource "aws_organizations_policy" "deployments" {
  name = "Deployments"
  type = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "OnlyReadingSets"
      Effect   = "Deny"
      Action   = "*"
      Resource = "*"
      Condition = {
        ArnLike = { "aws:PrincipalArn" = "arn:aws:iam::*:role/aws-reserved/sso.amazonaws.com/*" }
        ArnNotLike = {
          "aws:PrincipalArn" = [for set in local.deployment_sets : "arn:aws:iam::*:role/aws-reserved/sso.amazonaws.com/*/AWSReservedSSO_${set}_*"]
        }
      }
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
    guardrails         = aws_organizations_policy.guardrails.id
    regions            = aws_organizations_policy.regions.id
    identity_providers = aws_organizations_policy.identity_providers.id
  }

  policy_id = each.value
  target_id = local.root_id
}

# On the OUs, not the root: a new account is created in the root, where Organizations makes
# the role, and moved after
resource "aws_organizations_policy_attachment" "bootstrap_role" {
  for_each = local.ou_ids

  policy_id = aws_organizations_policy.bootstrap_role.id
  target_id = each.value
}

resource "aws_organizations_policy_attachment" "deployments" {
  policy_id = aws_organizations_policy.deployments.id
  target_id = local.ou_ids["Deployments"]
}

resource "aws_organizations_policy_attachment" "suspended" {
  policy_id = aws_organizations_policy.suspended.id
  target_id = local.ou_ids["Suspended"]
}
