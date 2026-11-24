terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

locals {
  dir      = "${path.module}/../../platform/aws/accounts"
  accounts = { for f in fileset(local.dir, "*.yaml") : trimsuffix(f, ".yaml") => yamldecode(file("${local.dir}/${f}")) }
}

# Needs a provider that lists accounts: management's, or a delegated administrator's
data "aws_organizations_organization" "this" {}

locals {
  by_name = {
    for account in data.aws_organizations_organization.this.non_master_accounts :
    account.name => account.id if account.state == "ACTIVE"
  }
}

output "accounts" {
  description = "Every member account in the catalogue, by slug."
  value       = local.accounts

  precondition {
    condition     = length(local.accounts) > 0
    error_message = "No accounts found in ${local.dir}."
  }
}

output "ids" {
  description = "Every account that exists, by slug, management's included as `management`."
  value = merge(
    { for slug, account in local.accounts : slug => local.by_name[account.name] if contains(keys(local.by_name), account.name) },
    { management = data.aws_organizations_organization.this.master_account_id },
  )
}
