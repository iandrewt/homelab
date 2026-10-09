data "aws_ssoadmin_instances" "this" {}

locals {
  instance_arn      = one(data.aws_ssoadmin_instances.this.arns)
  identity_store_id = one(data.aws_ssoadmin_instances.this.identity_store_ids)

  # A team owns state once it has a directory in tofu/; its Entra group is the same name,
  # capitalised
  state_teams = toset([for f in fileset("${path.module}/../../..", "**/tofu.tf") : title(split("/", f)[0])])

  permission_sets = {
    Administrator = { description = "Administers an account", managed = ["AdministratorAccess"] }
    SecurityAudit = { description = "Reads configuration and findings", managed = ["SecurityAudit"] }
    TofuState = {
      description = "OpenTofu state: becomes a team's role in platform/aws/state"
      inline = jsonencode({
        Version = "2012-10-17"
        Statement = [{
          Effect   = "Allow"
          Action   = "sts:AssumeRole"
          Resource = "arn:aws:iam::${local.account_ids["Platform"]}:role/tofu-state/*"
        }]
      })
    }
  }

  members = keys(local.account_ids)

  assignments = concat(
    [for account in local.members : { set = "Administrator", group = "Administrators", account = account }],
    [for account in local.members : { set = "SecurityAudit", group = "Security", account = account }],
    [
      { set = "Administrator", group = "Network", account = "Network" },
      { set = "Administrator", group = "Platform", account = "Platform" },
      { set = "Administrator", group = "Platform", account = "Sandbox" },
    ],
    [for team in local.state_teams : { set = "TofuState", group = team, account = "Platform" }],
  )
}

# Entra's, synced by SCIM
data "aws_identitystore_group" "this" {
  for_each = toset(local.assignments[*].group)

  identity_store_id = local.identity_store_id

  alternate_identifier {
    unique_attribute {
      attribute_path  = "DisplayName"
      attribute_value = each.key
    }
  }
}

resource "aws_ssoadmin_permission_set" "this" {
  for_each = local.permission_sets

  instance_arn = local.instance_arn
  name         = each.key
  description  = each.value.description
}

resource "aws_ssoadmin_managed_policy_attachment" "this" {
  for_each = merge([
    for set, config in local.permission_sets : {
      for policy in try(config.managed, []) : "${set}/${policy}" => { set = set, policy = policy }
    }
  ]...)

  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.this[each.value.set].arn
  managed_policy_arn = "arn:aws:iam::aws:policy/${each.value.policy}"
}

resource "aws_ssoadmin_permission_set_inline_policy" "this" {
  for_each = { for set, config in local.permission_sets : set => config.inline if can(config.inline) }

  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.this[each.key].arn
  inline_policy      = each.value
}

resource "aws_ssoadmin_account_assignment" "this" {
  for_each = { for a in local.assignments : "${a.set}/${a.group}/${a.account}" => a }

  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.this[each.value.set].arn
  principal_type     = "GROUP"
  principal_id       = data.aws_identitystore_group.this[each.value.group].group_id
  target_type        = "AWS_ACCOUNT"
  target_id          = local.account_ids[each.value.account]
}
