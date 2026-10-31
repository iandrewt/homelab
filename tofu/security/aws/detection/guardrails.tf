data "aws_region" "current" {}

locals {
  # logging's: the organisation trail, every account's and region's events
  trail_log_group = "/aws/cloudtrail/management-events"

  # The rest of Identity Center's events are sign-ins to the portal
  sso_writes = join(" || ", [for verb in ["Attach", "Create", "Delete", "Detach", "Provision", "Put", "Update"] : "($.eventName = ${verb}*)"])

  # Each is a change to the guardrails, or a use of root, that someone should have meant
  guardrails = {
    organizations   = "($.eventSource = organizations.amazonaws.com) && ($.readOnly IS FALSE)"
    identity-center = "($.eventSource = sso.amazonaws.com) && (${local.sso_writes})"
    scim-tokens     = "($.eventSource = sso-directory.amazonaws.com) && (($.eventName = CreateBearerToken) || ($.eventName = DeleteBearerToken))"
    keys            = "($.eventSource = kms.amazonaws.com) && (($.eventName = DisableKey) || ($.eventName = PutKeyPolicy) || ($.eventName = ScheduleKeyDeletion))"
    trails          = "($.eventSource = cloudtrail.amazonaws.com) && (($.eventName = DeleteTrail) || ($.eventName = PutEventSelectors) || ($.eventName = StopLogging) || ($.eventName = UpdateTrail))"
    root            = "($.userIdentity.type = Root) && ($.userIdentity.invokedBy NOT EXISTS) && ($.eventType != AwsServiceEvent)"
  }
}

resource "aws_cloudwatch_log_metric_filter" "guardrails" {
  for_each = local.guardrails

  name           = each.key
  log_group_name = local.trail_log_group
  pattern        = "{ ${each.value} }"

  metric_transformation {
    namespace = "Guardrails"
    name      = each.key
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "guardrails" {
  for_each = local.guardrails

  alarm_name        = "guardrails-${each.key}"
  alarm_description = "Guardrails, ${each.key}: its events are in ${local.trail_log_group}, in Security."

  namespace           = "Guardrails"
  metric_name         = aws_cloudwatch_log_metric_filter.guardrails[each.key].metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.findings[data.aws_region.current.region].arn]
}
