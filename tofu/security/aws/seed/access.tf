data "aws_ssoadmin_instances" "this" {}

locals {
  sso_instance_arn = one(data.aws_ssoadmin_instances.this.arns)

  # Users, not groups, as AWS advises: whoever manages a group would decide who reaches
  # management. By Identity Store ID, which a user made again under the same name doesn't get.
  management_administrators = ["499e64f8-70c1-70e6-0b34-e78a86877e2b"]
}

# Only the management account can change a permission set provisioned in it, so its own are here
# rather than in identity/aws/identity-center; and here rather than in org, since who reaches
# management is the seed's
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

resource "aws_ssoadmin_account_assignment" "management_administrator" {
  for_each = toset(local.management_administrators)

  instance_arn       = local.sso_instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.management_administrator.arn
  principal_type     = "USER"
  principal_id       = each.key
  target_type        = "AWS_ACCOUNT"
  target_id          = local.management_id
}
