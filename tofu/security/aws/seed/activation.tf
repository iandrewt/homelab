variable "activate" {
  description = "The tiers to open, each for one GitHub Actions run, by its run ID: org (deploy-org), release (images-release). Apply without it to close them."
  type        = map(string)
  default     = {}

  validation {
    condition     = alltrue([for tier, run in var.activate : contains(["org", "release"], tier) && can(regex("^[0-9]+$", run))])
    error_message = "activate takes org and release, each a run's numeric ID."
  }
}

# Where images-release is, as the bootstrap role: it lives in signing's layer, but what it may do is
# the seed's, so no CI role ever changes it
provider "aws" {
  alias   = "artifact_signing"
  region  = "ap-southeast-2"
  profile = "homelab-artifact-signing-bootstrap"

  default_tags {
    tags = { managed-by = "opentofu", layer = "security/aws/seed" }
  }
}

data "aws_kms_alias" "release" {
  provider = aws.artifact_signing

  name = "alias/images"
}

# For one run, by its ID from trusted identity propagation, so it holds nothing until the GitHub
# providers carry it; and only sessions issued in the next 15 minutes
locals {
  activation = { for tier, run in var.activate : tier => {
    StringEquals = { "aws:TransactionContext/IdentityProvider/run_id" = run }
    DateLessThan = { "aws:TokenIssueTime" = timeadd(plantimestamp(), "15m") }
  } }
}

# Capped by deploy-org's boundary, whatever it says
resource "aws_iam_role_policy" "activate_org" {
  count = contains(keys(var.activate), "org") ? 1 : 0

  role = aws_iam_role.deploy_org.id
  name = "activation"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "*"
      Resource  = "*"
      Condition = local.activation["org"]
    }]
  })
}

resource "aws_iam_role_policy" "activate_release" {
  count    = contains(keys(var.activate), "release") ? 1 : 0
  provider = aws.artifact_signing

  role = "images-release"
  name = "activation"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "kms:Sign"
      Resource  = data.aws_kms_alias.release.target_key_arn
      Condition = local.activation["release"]
    }]
  })
}
