data "aws_caller_identity" "current" {}

locals {
  # As the repository's immutable subject claim names it, the owner and repository by ID too, so a renamed
  # or recreated one can't take the role
  repository = "iandrewt@12795256/homelab@1394808425"
}

data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}
