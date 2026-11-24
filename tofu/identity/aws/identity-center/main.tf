data "aws_ssoadmin_instances" "this" {}

module "tenancy" {
  source = "../../../modules/tenancy"
}

locals {
  instance_arn      = one(data.aws_ssoadmin_instances.this.arns)
  identity_store_id = one(data.aws_ssoadmin_instances.this.identity_store_ids)

  state_teams = toset([for team in module.tenancy.teams : team.title])

  accounts    = module.accounts.accounts
  account_ids = module.accounts.ids

  trail_bucket = "arn:aws:s3:::cloudtrail-${local.account_ids["log-archive"]}-ap-southeast-2-an"

  permission_sets = {
    Administrator = { description = "Administers an account", managed = ["AdministratorAccess"] }
    SecurityAudit = { description = "Reads configuration and findings", managed = ["SecurityAudit"] }
    LogReader = {
      description = "Reads the organisation's trail"
      inline = jsonencode({
        Version = "2012-10-17"
        Statement = [
          {
            Sid      = "Trail"
            Effect   = "Allow"
            Action   = ["s3:GetObject", "s3:ListBucket"]
            Resource = [local.trail_bucket, "${local.trail_bucket}/*"]
          },
          {
            Sid       = "TrailKey"
            Effect    = "Allow"
            Action    = "kms:Decrypt"
            Resource  = "arn:aws:kms:ap-southeast-2:${local.account_ids["log-archive"]}:key/*"
            Condition = { "ForAnyValue:StringEquals" = { "kms:ResourceAliases" = "alias/cloudtrail" } }
          },
        ]
      })
    }
    TofuState = {
      description = "OpenTofu state: becomes a team's role in security/aws/state"
      inline = jsonencode({
        Version = "2012-10-17"
        Statement = [{
          Effect   = "Allow"
          Action   = "sts:AssumeRole"
          Resource = "arn:aws:iam::${local.account_ids["tofu-state"]}:role/tofu-state/*"
        }]
      })
    }
  }

  members = [for slug in keys(local.accounts) : slug if contains(keys(local.account_ids), slug)]

  # No one administers an account in Deployments but the bootstrap role
  deployments   = toset(data.aws_organizations_organizational_unit_descendant_accounts.deployments.accounts[*].id)
  administered  = [for slug in local.members : slug if !contains(local.deployments, local.account_ids[slug])]
  administrator = { for slug in local.administered : slug => toset(["Administrators", module.tenancy.teams[local.accounts[slug].owner].title]) }

  assignments = concat(
    flatten([for slug, groups in local.administrator : [for group in groups : { set = "Administrator", group = group, account = slug }]]),
    [for slug in local.members : { set = "SecurityAudit", group = "Security", account = slug }],
    [{ set = "LogReader", group = "Security", account = "log-archive" }],
    [for team in local.state_teams : { set = "TofuState", group = team, account = "tofu-state" }],
  )
}

data "aws_organizations_organizational_units" "top" {
  provider = aws.management

  parent_id = data.aws_organizations_organization.this.roots[0].id
}

data "aws_organizations_organization" "this" {
  provider = aws.management
}

data "aws_organizations_organizational_unit_descendant_accounts" "deployments" {
  provider = aws.management

  parent_id = one([for ou in data.aws_organizations_organizational_units.top.children : ou.id if ou.name == "Deployments"])
}

# Entra's, synced by SCIM: every group identity/entra makes, whose members it provisions
data "aws_identitystore_group" "this" {
  for_each = module.tenancy.groups

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
