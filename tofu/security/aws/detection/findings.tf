locals {
  alerts = {
    guardduty = {
      pattern = {
        source        = ["aws.guardduty"]
        "detail-type" = ["GuardDuty Finding"]
        detail = {
          # Root use is Low, and the break-glass path
          "$or" = [
            { severity = [{ numeric = [">=", 4] }] },
            { type = [{ prefix = "Policy:IAMUser/RootCredentialUsage" }] },
          ]
        }
      }
      paths = {
        severity = "$.detail.severity"
        title    = "$.detail.title"
        type     = "$.detail.type"
        account  = "$.detail.accountId"
        region   = "$.region"
      }
      template = "\"GuardDuty, severity <severity>: <title> - <type>, in <account>, <region>\""
    }
    access-analyzer = {
      pattern = {
        source        = ["aws.access-analyzer"]
        "detail-type" = ["Access Analyzer Finding"]
        detail        = { status = ["ACTIVE"] }
      }
      paths = {
        type     = "$.detail.resourceType"
        resource = "$.detail.resource"
        public   = "$.detail.isPublic"
        account  = "$.detail.accountId"
        region   = "$.region"
      }
      template = "\"Access Analyzer: <type> <resource> is reachable from outside the organisation (public: <public>), in <account>, <region>\""
    }
  }

  region_alerts = {
    for pair in setproduct(local.regions, keys(local.alerts)) :
    "${pair[0]}/${pair[1]}" => { region = pair[0], alert = pair[1] }
  }
}

resource "aws_sns_topic" "findings" {
  for_each = local.regions

  region = each.key
  name   = "findings"
}

resource "aws_sns_topic_subscription" "findings" {
  for_each = local.regions

  region    = each.key
  topic_arn = aws_sns_topic.findings[each.key].arn
  protocol  = "email"
  endpoint  = data.aws_organizations_organization.this.master_account_email
}

check "findings_confirmed" {
  assert {
    condition     = !anytrue([for s in aws_sns_topic_subscription.findings : s.pending_confirmation])
    error_message = "Unconfirmed: findings in ${join(", ", [for region, s in aws_sns_topic_subscription.findings : region if s.pending_confirmation])}. Confirm its email."
  }
}

resource "aws_cloudwatch_event_rule" "findings" {
  for_each = local.region_alerts

  region        = each.value.region
  name          = "${each.value.alert}-findings"
  event_pattern = jsonencode(local.alerts[each.value.alert].pattern)
}

resource "aws_cloudwatch_event_target" "findings" {
  for_each = local.region_alerts

  region = each.value.region
  rule   = aws_cloudwatch_event_rule.findings[each.key].name
  arn    = aws_sns_topic.findings[each.value.region].arn

  input_transformer {
    input_paths    = local.alerts[each.value.alert].paths
    input_template = local.alerts[each.value.alert].template
  }
}

resource "aws_sns_topic_policy" "findings" {
  for_each = local.regions

  region = each.key
  arn    = aws_sns_topic.findings[each.key].arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [{
        Sid       = "EventBridgePublishes"
        Effect    = "Allow"
        Principal = { Service = "events.amazonaws.com" }
        Action    = "sns:Publish"
        Resource  = aws_sns_topic.findings[each.key].arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = [for key, rule in aws_cloudwatch_event_rule.findings : rule.arn if local.region_alerts[key].region == each.key]
          }
        }
      }],
      each.key == data.aws_region.current.region ? [{
        Sid       = "AlarmsPublish"
        Effect    = "Allow"
        Principal = { Service = "cloudwatch.amazonaws.com" }
        Action    = "sns:Publish"
        Resource  = aws_sns_topic.findings[each.key].arn
        Condition = {
          ArnEquals = { "aws:SourceArn" = [for alarm in aws_cloudwatch_metric_alarm.guardrails : alarm.arn] }
        }
      }] : [],
    )
  })
}
