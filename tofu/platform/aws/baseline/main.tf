data "aws_regions" "enabled" {
  for_each = local.accounts
  provider = aws.account[each.key]
}

resource "aws_iam_account_alias" "this" {
  for_each = data.aws_regions.enabled
  provider = aws.account[each.key]

  account_alias = "iandrewt-${lower(replace(each.key, " ", "-"))}"
}

locals {
  enabled = merge([
    for account, regions in data.aws_regions.enabled : {
      for region in regions.names : "${account}/${region}" => { account = account, region = region }
    }
  ]...)
}

resource "aws_ebs_encryption_by_default" "this" {
  for_each = local.enabled
  provider = aws.account[each.value.account]

  region  = each.value.region
  enabled = true
}

# Once per account and region: the SCP stops another being made, and the check below finds
# one anyway
resource "terraform_data" "delete_default_vpcs" {
  for_each = local.enabled

  provisioner "local-exec" {
    command = "${path.module}/delete-default-vpcs.sh"

    environment = {
      AWS_PROFILE = local.profile
      AWS_REGION  = each.value.region
      ROLE_ARN    = lookup(local.bootstrap_roles, each.value.account, "")
    }
  }
}

data "aws_vpcs" "default" {
  for_each = local.enabled
  provider = aws.account[each.value.account]

  region = each.value.region

  filter {
    name   = "is-default"
    values = ["true"]
  }

  depends_on = [terraform_data.delete_default_vpcs]
}

check "no_default_vpcs" {
  assert {
    condition     = alltrue([for vpcs in data.aws_vpcs.default : length(vpcs.ids) == 0])
    error_message = "Default VPCs in ${join(", ", [for key, vpcs in data.aws_vpcs.default : key if length(vpcs.ids) > 0])}: apply with -replace on their terraform_data.delete_default_vpcs."
  }
}

# GitHub Actions' tokens, for a role of the account's own to trust; the provider alone grants nothing.
# Not management's, where nothing runs.
resource "aws_iam_openid_connect_provider" "github" {
  for_each = setsubtract(local.accounts, ["Management"])
  provider = aws.account[each.key]

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}
