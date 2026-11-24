module "regions" {
  source = "../../../modules/regions"
}

locals {
  # Organizations, IAM and Identity Center log in us-east-1; STS where it's called
  global   = "us-east-1"
  regions  = toset(concat([local.global], module.regions.names))
  api_call = "AWS API Call via CloudTrail"

  # Every change to what bounds the organisation, and to this layer's own
  tripwires = {
    organisation = { regions = [local.global], detail = { eventSource = ["organizations.amazonaws.com"], readOnly = [false] } }
    iam          = { regions = [local.global], detail = { eventSource = ["iam.amazonaws.com"], readOnly = [false] } }
    sso          = { regions = [local.global], detail = { eventSource = ["sso.amazonaws.com"], readOnly = [false] } }
    tripwires    = { regions = local.regions, detail = { eventSource = ["events.amazonaws.com", "sns.amazonaws.com"], readOnly = [false] } }
    trail = {
      regions = local.regions
      detail  = { eventSource = ["cloudtrail.amazonaws.com"], eventName = ["DeleteTrail", "PutEventSelectors", "StopLogging", "UpdateTrail"] }
    }
    # Break-glass, once CI applies: until then every apply is a person's, and this would mail each
    bootstrap = {
      regions  = local.regions
      disabled = true
      detail = {
        eventSource       = ["sts.amazonaws.com"]
        eventName         = ["AssumeRole"]
        requestParameters = { roleArn = [{ wildcard = "arn:aws:iam::*:role/OrganizationAccountAccessRole" }] }
      }
    }
  }

  region_tripwires = merge([
    for name, tripwire in local.tripwires : { for region in tripwire.regions : "${region}/${name}" => { region = region, name = name } }
  ]...)
}

resource "aws_sns_topic" "tripwires" {
  for_each = local.regions

  region = each.key
  name   = "tripwires"
}

resource "aws_sns_topic_subscription" "tripwires" {
  for_each = local.regions

  region    = each.key
  topic_arn = aws_sns_topic.tripwires[each.key].arn
  protocol  = "email"
  endpoint  = data.aws_organizations_organization.this.master_account_email
}

check "tripwires_confirmed" {
  assert {
    condition     = !anytrue([for s in aws_sns_topic_subscription.tripwires : s.pending_confirmation])
    error_message = "Unconfirmed: tripwires in ${join(", ", [for region, s in aws_sns_topic_subscription.tripwires : region if s.pending_confirmation])}. Confirm its email."
  }
}

resource "aws_cloudwatch_event_rule" "tripwires" {
  for_each = local.region_tripwires

  region = each.value.region
  name   = "${each.value.name}-tripwire"
  state  = try(local.tripwires[each.value.name].disabled, false) ? "DISABLED" : "ENABLED"

  event_pattern = jsonencode({
    "detail-type" = [local.api_call]
    detail        = local.tripwires[each.value.name].detail
  })
}

resource "aws_cloudwatch_event_target" "tripwires" {
  for_each = local.region_tripwires

  region = each.value.region
  rule   = aws_cloudwatch_event_rule.tripwires[each.key].name
  arn    = aws_sns_topic.tripwires[each.value.region].arn

  input_transformer {
    input_paths = {
      event   = "$.detail.eventName"
      source  = "$.detail.eventSource"
      who     = "$.detail.userIdentity.arn"
      origin  = "$.detail.userIdentity.onBehalfOf.subject"
      account = "$.account"
      region  = "$.region"
      error   = "$.detail.errorCode"
    }
    input_template = "\"Tripwire ${each.value.name}: <event> on <source> by <who> (origin: <origin>), in <account>, <region>. Error: <error>\""
  }
}

resource "aws_sns_topic_policy" "tripwires" {
  for_each = local.regions

  region = each.key
  arn    = aws_sns_topic.tripwires[each.key].arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "EventBridgePublishes"
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sns:Publish"
      Resource  = aws_sns_topic.tripwires[each.key].arn
      Condition = {
        ArnEquals = {
          "aws:SourceArn" = [for key, rule in aws_cloudwatch_event_rule.tripwires : rule.arn if local.region_tripwires[key].region == each.key]
        }
      }
    }]
  })
}
