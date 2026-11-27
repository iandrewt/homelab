# Identity Center's and account access manager's administrators can assign roles in any member, so
# neither is Security Tooling
resource "aws_organizations_delegated_administrator" "this" {
  for_each = {
    "access-analyzer.amazonaws.com" = "security-tooling"
    "account-access.amazonaws.com"  = "identity"
    "iam.amazonaws.com"             = "security-tooling"
    "sso.amazonaws.com"             = "identity"
  }

  account_id        = local.account_ids[each.value]
  service_principal = each.key

  depends_on = [aws_organizations_organization.this]
}

resource "aws_cloudtrail_organization_delegated_admin_account" "security" {
  account_id = local.account_ids["security-tooling"]

  depends_on = [aws_organizations_organization.this]
}

resource "aws_guardduty_organization_admin_account" "security" {
  for_each = toset(local.regions)

  region           = each.key
  admin_account_id = local.account_ids["security-tooling"]

  depends_on = [aws_organizations_organization.this, aws_account_region.this]
}

resource "aws_iam_organizations_features" "this" {
  enabled_features = ["RootCredentialsManagement", "RootSessions"]

  depends_on = [aws_organizations_organization.this]
}

import {
  to = aws_organizations_delegated_administrator.this["account-access.amazonaws.com"]
  id = "${local.account_ids["identity"]}/account-access.amazonaws.com"
}

data "aws_ssoadmin_instances" "this" {}

# Deleting it deletes every assignment, and can't be undone
resource "aws_accountaccess_application" "this" {
  identity_source {
    identity_center {
      instance_arn = one(data.aws_ssoadmin_instances.this.arns)
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

import {
  to = aws_accountaccess_application.this
  id = "arn:aws:account-access:ap-southeast-2:${aws_organizations_organization.this.master_account_id}:application/7SyByTtMnlwXo8B2"
}
