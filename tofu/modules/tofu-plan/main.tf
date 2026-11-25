terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

variable "environments" {
  description = "The GitHub environments whose plans read this account: its owner's, and the org tier's."
  type        = list(string)
}

variable "permission_sets" {
  description = "The Identity Center permission sets whose sessions in the account plan here."
  type        = list(string)
}

data "aws_caller_identity" "current" {}

module "github" {
  source = "../github"
}

locals {
  account = data.aws_caller_identity.current.account_id
}

# One role for every plan, a person's or CI's, so a plan that works locally works in a PR. It reads
# contents too, as drift and ephemeral values need: what a plan sees is scoped by who may assume it.
# The account's GitHub provider must exist first
resource "aws_iam_role" "this" {
  name        = "tofu-plan"
  path        = "/org/"
  description = "Reads what OpenTofu refreshes, for a plan"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "Workflow"
        Effect    = "Allow"
        Principal = { Federated = "arn:aws:iam::${local.account}:oidc-provider/token.actions.githubusercontent.com" }
        Action    = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud"              = "sts.amazonaws.com"
            "token.actions.githubusercontent.com:job_workflow_ref" = module.github.workflow
            "token.actions.githubusercontent.com:environment"      = var.environments
            "token.actions.githubusercontent.com:actor_id"         = module.github.planners
          }
          StringLike = { "token.actions.githubusercontent.com:sub" = "repo:${module.github.repository}:environment:tofu-plan-*" }
        }
      },
      {
        Sid       = "People"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${local.account}:root" }
        Action    = "sts:AssumeRole"
        Condition = {
          ArnLike = {
            "aws:PrincipalArn" = [for set in var.permission_sets : "arn:aws:iam::${local.account}:role/aws-reserved/sso.amazonaws.com/*/AWSReservedSSO_${set}_*"]
          }
        }
      },
    ]
  })
}

# ReadOnlyAccess, and AWS's own read policies for what's newer than it
resource "aws_iam_role_policy_attachment" "read_only" {
  for_each = toset(["ReadOnlyAccess", "PricingPlanManagerReadonlyAccess"])

  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/${each.key}"
}
