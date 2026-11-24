module "regions" {
  source = "../../../modules/regions"
}

locals {
  regions = toset(module.regions.names)

  features = ["S3_DATA_EVENTS", "EKS_AUDIT_LOGS"]
  runtime  = ["sandbox"]

  region_features = {
    for pair in setproduct(local.regions, local.features) :
    "${pair[0]}/${pair[1]}" => { region = pair[0], feature = pair[1] }
  }
}

data "aws_organizations_organization" "this" {}

module "accounts" {
  source = "../../../modules/accounts"
}

locals {
  account_ids = module.accounts.ids
}

resource "aws_guardduty_detector" "this" {
  for_each = local.regions

  region = each.key
  enable = true
}

resource "aws_guardduty_detector_feature" "this" {
  for_each = local.region_features

  region      = each.value.region
  detector_id = aws_guardduty_detector.this[each.value.region].id
  name        = each.value.feature
  status      = "ENABLED"
}

resource "aws_guardduty_organization_configuration" "this" {
  for_each = local.regions

  region                           = each.key
  detector_id                      = aws_guardduty_detector.this[each.key].id
  auto_enable_organization_members = "ALL"
}

resource "aws_guardduty_organization_configuration_feature" "this" {
  for_each = local.region_features

  region      = each.value.region
  detector_id = aws_guardduty_detector.this[each.value.region].id
  name        = each.value.feature
  auto_enable = "ALL"
}

# A member exists only once auto-enabling has reached it, which can take a second apply
resource "aws_guardduty_member_detector_feature" "runtime" {
  for_each = {
    for pair in setproduct(local.regions, local.runtime) :
    "${pair[0]}/${pair[1]}" => { region = pair[0], account = pair[1] }
  }

  region      = each.value.region
  detector_id = aws_guardduty_detector.this[each.value.region].id
  account_id  = local.account_ids[each.value.account]
  name        = "RUNTIME_MONITORING"
  status      = "ENABLED"

  dynamic "additional_configuration" {
    for_each = ["EKS_ADDON_MANAGEMENT", "ECS_FARGATE_AGENT_MANAGEMENT", "EC2_AGENT_MANAGEMENT"]

    content {
      name   = additional_configuration.value
      status = "ENABLED"
    }
  }

  depends_on = [aws_guardduty_organization_configuration.this]
}

resource "aws_accessanalyzer_analyzer" "organisation" {
  for_each = local.regions

  region        = each.key
  analyzer_name = "organisation"
  type          = "ORGANIZATION"
}

# Identity Center's SAML provider counts as outside the organisation, so every role it makes
# would be a finding
resource "aws_accessanalyzer_archive_rule" "identity_center" {
  for_each = local.regions

  region        = each.key
  analyzer_name = aws_accessanalyzer_analyzer.organisation[each.key].analyzer_name
  rule_name     = "identity-center"

  filter {
    criteria = "resourceType"
    eq       = ["AWS::IAM::Role"]
  }

  filter {
    criteria = "resource"
    contains = [":role/aws-reserved/sso.amazonaws.com/"]
  }

  filter {
    criteria = "principal.Federated"
    contains = [":saml-provider/AWSSSO_"]
  }
}
