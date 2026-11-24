locals {
  budgets = {
    Organisation = { limit = "75", accounts = [] }
    Sandbox      = { limit = "20", accounts = [local.account_ids["sandbox"]] }
  }
}

resource "aws_budgets_budget" "this" {
  for_each = local.budgets

  name         = each.key
  budget_type  = "COST"
  limit_amount = each.value.limit
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "cost_filter" {
    for_each = length(each.value.accounts) > 0 ? [each.value.accounts] : []

    content {
      name   = "LinkedAccount"
      values = cost_filter.value
    }
  }

  dynamic "notification" {
    for_each = ["ACTUAL", "FORECASTED"]

    content {
      notification_type          = notification.value
      comparison_operator        = "GREATER_THAN"
      threshold                  = 100
      threshold_type             = "PERCENTAGE"
      subscriber_email_addresses = [local.management_email]
    }
  }
}

resource "aws_ce_anomaly_monitor" "services" {
  name              = "Services"
  monitor_type      = "DIMENSIONAL"
  monitor_dimension = "SERVICE"
}

resource "aws_ce_anomaly_subscription" "email" {
  name             = "Email"
  frequency        = "DAILY"
  monitor_arn_list = [aws_ce_anomaly_monitor.services.arn]

  subscriber {
    type    = "EMAIL"
    address = local.management_email
  }

  threshold_expression {
    dimension {
      key           = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
      match_options = ["GREATER_THAN_OR_EQUAL"]
      values        = ["5"]
    }
  }
}
