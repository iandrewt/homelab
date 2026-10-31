locals {
  regions = ["ap-southeast-2", "ap-southeast-4"]

  # Paths, nested with a slash: an OU can't move once made, but an account can
  organizational_units = ["Security", "Infrastructure", "Sandbox", "Suspended"]

  # baseline names them too, since a provider's for_each can't read them from here
  accounts = {
    "Log Archive" = "Security"
    "Security"    = "Security"
    "State"       = "Security"
    "Network"     = "Infrastructure"
    "Platform"    = "Infrastructure"
    "Sandbox"     = "Sandbox"
  }
}

resource "aws_organizations_organization" "this" {
  feature_set = "ALL"

  aws_service_access_principals = [
    "access-analyzer.amazonaws.com",
    "account.amazonaws.com",
    "cloudtrail.amazonaws.com",
    "compute-optimizer.amazonaws.com",
    "cost-optimization-hub.bcm.amazonaws.com",
    "ec2.amazonaws.com",
    "health.amazonaws.com",
    "guardduty.amazonaws.com",
    "malware-protection.guardduty.amazonaws.com",
    "iam.amazonaws.com",
    "member.org.stacksets.cloudformation.amazonaws.com",
    "notifications.amazonaws.com",
    "ram.amazonaws.com",
    "resource-explorer-2.amazonaws.com",
    "sso.amazonaws.com",
  ]

  enabled_policy_types = [
    "AISERVICES_OPT_OUT_POLICY",
    "DECLARATIVE_POLICY_EC2",
    "RESOURCE_CONTROL_POLICY",
    "S3_POLICY",
    "SERVICE_CONTROL_POLICY",
  ]
}

locals {
  root_id          = aws_organizations_organization.this.roots[0].id
  management_id    = aws_organizations_organization.this.master_account_id
  management_email = aws_organizations_organization.this.master_account_email

  account_ids = merge(
    { Management = local.management_id },
    { for name, account in aws_organizations_account.this : name => account.id },
  )
}

resource "aws_organizations_organizational_unit" "top" {
  for_each = toset([for path in local.organizational_units : path if !strcontains(path, "/")])

  name      = each.key
  parent_id = local.root_id
}

# A third level is a third resource
resource "aws_organizations_organizational_unit" "nested" {
  for_each = {
    for path in local.organizational_units : path => regex("^(.+)/([^/]+)$", path) if strcontains(path, "/")
  }

  name      = each.value[1]
  parent_id = aws_organizations_organizational_unit.top[each.value[0]].id
}

locals {
  ou_ids = merge(
    { for path, ou in aws_organizations_organizational_unit.top : path => ou.id },
    { for path, ou in aws_organizations_organizational_unit.nested : path => ou.id },
  )
}

resource "aws_organizations_account" "this" {
  for_each = local.accounts

  name      = each.key
  email     = replace(local.management_email, "/^([^+@]+)[^@]*@/", "$1+${lower(replace(each.key, " ", "-"))}@")
  parent_id = local.ou_ids[each.value]

  lifecycle {
    prevent_destroy = true
    # A root email changes by a verified two-step update tofu can't make; this names a new account's
    ignore_changes = [email]
  }
}

data "aws_account_regions" "management" {}

locals {
  opt_in_regions = [
    for region in data.aws_account_regions.management.regions :
    region.region_name if contains(local.regions, region.region_name) && region.region_opt_status != "ENABLED_BY_DEFAULT"
  ]
}

resource "aws_account_region" "this" {
  for_each = {
    for pair in setproduct(keys(local.account_ids), local.opt_in_regions) :
    "${pair[0]}/${pair[1]}" => { account = pair[0], region = pair[1] }
  }

  # The Account API refuses the management account's own ID
  account_id  = each.value.account == "Management" ? null : local.account_ids[each.value.account]
  region_name = each.value.region
  enabled     = true
}

# Management's own is set by hand, so no personal detail is in the repo; every other account
# copies it
data "aws_account_primary_contact" "management" {}

locals {
  primary_contact = {
    for field, value in data.aws_account_primary_contact.management : field => value if value != ""
  }
}

resource "aws_account_primary_contact" "members" {
  for_each = aws_organizations_account.this

  account_id         = each.value.id
  full_name          = local.primary_contact.full_name
  company_name       = lookup(local.primary_contact, "company_name", null)
  address_line_1     = local.primary_contact.address_line_1
  address_line_2     = lookup(local.primary_contact, "address_line_2", null)
  address_line_3     = lookup(local.primary_contact, "address_line_3", null)
  city               = local.primary_contact.city
  district_or_county = lookup(local.primary_contact, "district_or_county", null)
  state_or_region    = lookup(local.primary_contact, "state_or_region", null)
  postal_code        = local.primary_contact.postal_code
  country_code       = local.primary_contact.country_code
  phone_number       = local.primary_contact.phone_number
  website_url        = lookup(local.primary_contact, "website_url", null)

  depends_on = [aws_organizations_organization.this]
}

resource "aws_account_alternate_contact" "this" {
  for_each = {
    for pair in setproduct(keys(local.account_ids), ["BILLING", "OPERATIONS", "SECURITY"]) :
    "${pair[0]}/${pair[1]}" => { account = pair[0], type = pair[1] }
  }

  account_id             = each.value.account == "Management" ? null : local.account_ids[each.value.account]
  alternate_contact_type = each.value.type
  name                   = data.aws_account_primary_contact.management.full_name
  title                  = "Owner"
  email_address          = local.management_email
  phone_number           = data.aws_account_primary_contact.management.phone_number

  depends_on = [aws_organizations_organization.this]
}
