data "aws_region" "current" {}

data "aws_caller_identity" "current" {}

locals {
  # logging's: the organisation trail, every account's and region's events
  trail_log_group = "/aws/cloudtrail/management-events"

  # The rest of Identity Center's events are sign-ins to the portal
  sso_writes = join(" || ", [for verb in ["Attach", "Create", "Delete", "Detach", "Provision", "Put", "Update"] : "$.eventName = ${verb}*"])

  # Each is a change to the guardrails, or a use of root, that someone should have meant. IAM's
  # is the management account's, which no SCP binds. One pattern holds them all, in 1024 characters
  guardrails = {
    organizations   = "$.eventSource = organizations.amazonaws.com && $.readOnly IS FALSE"
    identity-center = "$.eventSource = sso.amazonaws.com && (${local.sso_writes})"
    scim-tokens     = "$.eventSource = sso-directory.amazonaws.com && ($.eventName = CreateBearerToken || $.eventName = DeleteBearerToken)"
    keys            = "$.eventSource = kms.amazonaws.com && ($.eventName = DisableKey || $.eventName = PutKeyPolicy || $.eventName = ScheduleKeyDeletion)"
    trails          = "$.eventSource = cloudtrail.amazonaws.com && ($.eventName = DeleteTrail || $.eventName = PutEventSelectors || $.eventName = StopLogging || $.eventName = UpdateTrail)"
    iam-users       = "$.eventSource = iam.amazonaws.com && ($.eventName = CreateUser || $.eventName = CreateAccessKey || $.eventName = CreateLoginProfile)"
    root            = "$.userIdentity.type = Root && $.userIdentity.invokedBy NOT EXISTS && $.eventType != AwsServiceEvent"
  }
}

data "archive_file" "guardrails" {
  type        = "zip"
  source_file = "${path.module}/guardrails.py"
  output_path = "${path.module}/.terraform/guardrails.zip"
}

resource "aws_iam_role" "guardrails" {
  name = "guardrails"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_cloudwatch_log_group" "guardrails" {
  name              = "/aws/lambda/guardrails"
  retention_in_days = 14
}

resource "aws_iam_role_policy" "guardrails" {
  role = aws_iam_role.guardrails.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.guardrails.arn}:log-stream:*"
      },
      {
        Effect   = "Allow"
        Action   = "sns:Publish"
        Resource = aws_sns_topic.findings[data.aws_region.current.region].arn
      },
      {
        Effect   = "Allow"
        Action   = "organizations:DescribeAccount"
        Resource = "arn:aws:organizations::${data.aws_organizations_organization.this.master_account_id}:account/${data.aws_organizations_organization.this.id}/*"
      },
    ]
  })
}

resource "aws_lambda_function" "guardrails" {
  function_name = "guardrails"
  description   = "Mails the organisation trail's guardrail events to the findings topic"
  role          = aws_iam_role.guardrails.arn
  runtime       = "python3.14"
  architectures = ["arm64"]
  handler       = "guardrails.handler"
  filename      = data.archive_file.guardrails.output_path
  code_sha256   = data.archive_file.guardrails.output_base64sha256
  timeout       = 30

  environment {
    variables = {
      TOPIC = aws_sns_topic.findings[data.aws_region.current.region].arn
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.guardrails.name
  }

  depends_on = [aws_iam_role_policy.guardrails]
}

resource "aws_lambda_permission" "guardrails" {
  function_name = aws_lambda_function.guardrails.function_name
  action        = "lambda:InvokeFunction"
  principal     = "logs.amazonaws.com"
  source_arn    = "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:${local.trail_log_group}:*"
}

resource "aws_cloudwatch_log_subscription_filter" "guardrails" {
  name            = "guardrails"
  log_group_name  = local.trail_log_group
  filter_pattern  = "{ ${join(" || ", [for pattern in values(local.guardrails) : "(${pattern})"])} }"
  destination_arn = aws_lambda_function.guardrails.arn

  depends_on = [aws_lambda_permission.guardrails]
}

# The mail itself says nothing when it fails
resource "aws_cloudwatch_metric_alarm" "undelivered" {
  alarm_name        = "guardrails-undelivered"
  alarm_description = "Guardrail events weren't mailed: https://${data.aws_region.current.region}.console.aws.amazon.com/cloudwatch/home?region=${data.aws_region.current.region}#logsV2:log-groups/log-group/${replace(urlencode(aws_cloudwatch_log_group.guardrails.name), "%", "$25")}"

  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.guardrails.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.findings[data.aws_region.current.region].arn]
}
