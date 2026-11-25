locals {
  management_id = data.aws_organizations_organization.this.master_account_id

  bootstrap_role = "arn:aws:iam::*:role/OrganizationAccountAccessRole"
}

module "github" {
  source = "../../../modules/github"
}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

# Assumable by the organisation's workflow on main, but it holds nothing: an activation adds a policy
# for one run, which the boundary caps
resource "aws_iam_role" "deploy_org" {
  name                 = "deploy-org"
  permissions_boundary = aws_iam_policy.deploy_org_boundary.arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud"              = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub"              = "repo:${module.github.repository}:environment:tofu-org"
          "token.actions.githubusercontent.com:job_workflow_ref" = module.github.workflow
          "token.actions.githubusercontent.com:actor_id"         = module.github.operator
        }
      }
    }]
  })
}

# What the organisation's layers need in management, and nothing that could widen deploy-org's own
# reach: no IAM but reads and the account alias, no Identity Center, EventBridge or SNS
resource "aws_iam_policy" "deploy_org_boundary" {
  name = "deploy-org-boundary"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Organisation"
        Effect   = "Allow"
        Action   = ["organizations:*", "account:*", "budgets:*", "ce:*"]
        Resource = "*"
      },
      {
        Sid    = "Delegation"
        Effect = "Allow"
        Action = [
          "cloudtrail:DeregisterOrganizationDelegatedAdminAccount",
          "cloudtrail:RegisterOrganizationDelegatedAdminAccount",
          "guardduty:DisableOrganizationAdminAccount",
          "guardduty:EnableOrganizationAdminAccount",
          "guardduty:ListOrganizationAdminAccounts",
          "iam:DisableOrganizationsRootCredentialsManagement",
          "iam:DisableOrganizationsRootSessions",
          "iam:EnableOrganizationsRootCredentialsManagement",
          "iam:EnableOrganizationsRootSessions",
          "iam:ListOrganizationsFeatures",
        ]
        Resource = "*"
      },
      {
        Sid      = "ServiceLinkedRoles"
        Effect   = "Allow"
        Action   = "iam:CreateServiceLinkedRole"
        Resource = "arn:aws:iam::${local.management_id}:role/aws-service-role/*"
      },
      {
        # Management's own baseline
        Sid    = "Baseline"
        Effect = "Allow"
        Action = [
          "ec2:DeleteInternetGateway",
          "ec2:DeleteSubnet",
          "ec2:DeleteVpc",
          "ec2:Describe*",
          "ec2:DetachInternetGateway",
          "ec2:DisableEbsEncryptionByDefault",
          "ec2:EnableEbsEncryptionByDefault",
          "ec2:GetEbsEncryptionByDefault",
          "iam:CreateAccountAlias",
          "iam:DeleteAccountAlias",
        ]
        Resource = "*"
      },
      {
        Sid      = "Reads"
        Effect   = "Allow"
        Action   = ["iam:Get*", "iam:List*", "sso:Describe*", "sso:Get*", "sso:List*"]
        Resource = "*"
      },
      {
        # SetContext carries trusted identity propagation into bootstrap's session
        Sid      = "Bootstrap"
        Effect   = "Allow"
        Action   = ["sts:AssumeRole", "sts:SetContext"]
        Resource = local.bootstrap_role
      },
      {
        # Delegating the organisation's policies, or changing a root's email, would outlast any activation
        Sid    = "NeverDestructive"
        Effect = "Deny"
        Action = [
          "account:AcceptPrimaryEmailUpdate",
          "account:StartPrimaryEmailUpdate",
          "organizations:CloseAccount",
          "organizations:DeleteOrganization",
          "organizations:DeleteResourcePolicy",
          "organizations:LeaveOrganization",
          "organizations:PutResourcePolicy",
          "organizations:RemoveAccountFromOrganization",
        ]
        Resource = "*"
      },
    ]
  })
}

# Management's plans: management's permission sets go to users alone, so its own administrators plan here
module "tofu_plan" {
  source = "../../../modules/tofu-plan"

  environments    = ["tofu-plan-org"]
  permission_sets = ["ManagementAdministrator"]

  depends_on = [aws_iam_openid_connect_provider.github]
}
