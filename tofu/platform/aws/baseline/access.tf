# What every member needs for CI to reach it: GitHub's issuer, which any workload's roles may
# federate through within the Identity providers SCP, and the roles tofu runs as. Management's are
# the seed's. Through bootstrap alone, since SCPs let nothing else change either

locals {
  members   = { for slug, account in local.catalogue : slug => account }
  team_tier = { for slug, account in local.catalogue : slug => account if account.tier == "team" }
}

module "github" {
  source = "../../../modules/github"
}

resource "aws_iam_openid_connect_provider" "github" {
  for_each = local.members
  provider = aws.account[each.key]

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

# A new account has none until this applies there, so that first apply is by hand
module "tofu_plan" {
  source   = "../../../modules/tofu-plan"
  for_each = { for slug, account in local.members : slug => account.owner }

  environments    = ["tofu-plan-${each.value}", "tofu-plan-org"]
  permission_sets = ["Administrator", "ReadOnly"]

  providers  = { aws = aws.account[each.key] }
  depends_on = [aws_iam_openid_connect_provider.github]
}

# CI's alone: a person applies only by break-glass, through bootstrap. Anything a team-tier account
# holds is inside the SCPs, so within them it administers the account
resource "aws_iam_role" "tofu_apply" {
  for_each = local.team_tier
  provider = aws.account[each.key]

  name        = "tofu-apply"
  path        = "/org/"
  description = "Applies the ${each.value.owner} team's layers, from CI"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github[each.key].arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud"              = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub"              = "repo:${module.github.repository}:environment:tofu-${each.value.owner}"
          "token.actions.githubusercontent.com:job_workflow_ref" = module.github.workflow
          "token.actions.githubusercontent.com:actor_id"         = module.github.operator
        }
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "tofu_apply" {
  for_each = local.team_tier
  provider = aws.account[each.key]

  role       = aws_iam_role.tofu_apply[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

# The old release key, and the role that signs with it, are what every ymir machine still trusts
# until C19 retires them: the release tier's, not the team's
resource "aws_iam_role_policy" "not_the_old_release_key" {
  provider = aws.account["artifact-distribution"]

  role = aws_iam_role.tofu_apply["artifact-distribution"].id
  name = "not-the-release-key"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "Key"
        Effect    = "Deny"
        NotAction = ["kms:Describe*", "kms:Get*", "kms:List*"]
        Resource  = "*"
        Condition = { "ForAnyValue:StringEquals" = { "kms:ResourceAliases" = "alias/images" } }
      },
      {
        Sid       = "Signer"
        Effect    = "Deny"
        NotAction = ["iam:Get*", "iam:List*"]
        Resource  = "arn:aws:iam::*:role/images-release"
      },
    ]
  })
}
