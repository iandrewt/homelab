resource "aws_iam_openid_connect_provider" "github" {
  for_each = { for slug in local.accounts : slug => module.accounts.ids[slug] }
  provider = aws.account[each.key]

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}
