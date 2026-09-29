resource "aws_organizations_delegated_administrator" "security" {
  for_each = toset([
    "access-analyzer.amazonaws.com",
    "iam.amazonaws.com",
    "sso.amazonaws.com",
  ])

  account_id        = local.account_ids["Security"]
  service_principal = each.key

  depends_on = [aws_organizations_organization.this]
}

resource "aws_cloudtrail_organization_delegated_admin_account" "security" {
  account_id = local.account_ids["Security"]

  depends_on = [aws_organizations_organization.this]
}

resource "aws_guardduty_organization_admin_account" "security" {
  for_each = toset(local.regions)

  region           = each.key
  admin_account_id = local.account_ids["Security"]

  depends_on = [aws_organizations_organization.this, aws_account_region.this]
}

resource "aws_iam_organizations_features" "this" {
  enabled_features = ["RootCredentialsManagement", "RootSessions"]

  depends_on = [aws_organizations_organization.this]
}
