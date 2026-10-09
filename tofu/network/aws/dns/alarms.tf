data "aws_organizations_organization" "this" {
  return_organization_only = true
}

resource "aws_sns_topic" "dnssec" {
  region = "us-east-1"
  name   = "dnssec"
}

resource "aws_sns_topic_subscription" "dnssec" {
  region    = "us-east-1"
  topic_arn = aws_sns_topic.dnssec.arn
  protocol  = "email"
  endpoint  = data.aws_organizations_organization.this.master_account_email
}

resource "aws_cloudwatch_metric_alarm" "dnssec" {
  for_each = {
    for pair in setproduct(keys(local.zones), ["DNSSECInternalFailure", "DNSSECKeySigningKeysNeedingAction"]) :
    "${pair[0]}/${pair[1]}" => { zone = local.zones[pair[0]], metric = pair[1] }
  }

  region            = "us-east-1"
  alarm_name        = "${each.value.zone.name} ${each.value.metric}"
  alarm_description = "Route53 can't sign ${each.value.zone.name}; validators will find it bogus once its signatures expire"

  namespace   = "AWS/Route53"
  metric_name = each.value.metric
  dimensions  = { HostedZoneId = each.value.zone.zone_id }
  statistic   = "Sum"

  # Published once every four hours
  period              = 14400
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.dnssec.arn]
  ok_actions    = [aws_sns_topic.dnssec.arn]
}
