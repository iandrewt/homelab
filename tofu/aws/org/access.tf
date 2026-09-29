data "aws_ssoadmin_instances" "this" {}

locals {
  sso_instance_arn  = one(data.aws_ssoadmin_instances.this.arns)
  identity_store_id = one(data.aws_ssoadmin_instances.this.identity_store_ids)

  management_administrators = ["Andrew Titmuss"]
}

# Only the management account can change a permission set provisioned in it, so its own
# are here rather than in identity
resource "aws_ssoadmin_permission_set" "management_administrator" {
  instance_arn     = local.sso_instance_arn
  name             = "ManagementAdministrator"
  description      = "The management account, and the one role into every member"
  session_duration = "PT1H"
}

resource "aws_ssoadmin_managed_policy_attachment" "management_administrator" {
  instance_arn       = local.sso_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.management_administrator.arn
  managed_policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

data "aws_identitystore_users" "this" {
  identity_store_id = local.identity_store_id
}

resource "aws_ssoadmin_account_assignment" "management_administrator" {
  for_each = toset(local.management_administrators)

  instance_arn       = local.sso_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.management_administrator.arn
  principal_type     = "USER"
  principal_id       = { for user in data.aws_identitystore_users.this.users : user.display_name => user.user_id }[each.key]
  target_type        = "AWS_ACCOUNT"
  target_id          = local.management_id
}
