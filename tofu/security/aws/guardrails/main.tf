# Platform's organisation, which Security only writes the policies of
data "aws_organizations_organization" "this" {}

data "aws_organizations_organizational_units" "top" {
  parent_id = local.root_id
}

locals {
  # Platform enables the opt-in ones, in platform/aws/org
  regions = ["ap-southeast-2", "ap-southeast-4"]

  organization_id = data.aws_organizations_organization.this.id
  root_id         = data.aws_organizations_organization.this.roots[0].id
  management_id   = data.aws_organizations_organization.this.master_account_id

  account_ids = { for a in data.aws_organizations_organization.this.non_master_accounts : a.name => a.id }
  ou_ids      = { for ou in data.aws_organizations_organizational_units.top.children : ou.name => ou.id }

  # platform/aws/org's
  management_administrator = "ManagementAdministrator"
}
