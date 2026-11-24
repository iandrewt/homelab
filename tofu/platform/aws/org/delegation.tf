# Identity Center's administrator can assign Administrator in any member, so it isn't Security Tooling
resource "aws_organizations_delegated_administrator" "this" {
  for_each = {
    "access-analyzer.amazonaws.com" = "security-tooling"
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
