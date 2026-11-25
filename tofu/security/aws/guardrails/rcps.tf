resource "aws_organizations_policy" "perimeter" {
  name = "Perimeter"
  type = "RESOURCE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "OnlyTheOrganisation"
        Effect    = "Deny"
        Principal = "*"
        Action = [
          "kms:*",
          "s3:*",
          "sts:AssumeRole",
          "sts:DecodeAuthorizationMessage",
          "sts:GetAccessKeyInfo",
          "sts:GetFederationToken",
          "sts:GetServiceBearerToken",
          "sts:GetSessionToken",
        ]
        Resource = "*"
        Condition = {
          StringNotEqualsIfExists = { "aws:PrincipalOrgID" = local.organization_id }
          StringNotEquals         = { "aws:PrincipalType" = "Anonymous" }
          BoolIfExists            = { "aws:PrincipalIsAWSService" = "false" }
        }
      },
      {
        Sid       = "AnonymousOnlyThroughTheOrganisation"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = "*"
        Condition = {
          StringEquals            = { "aws:PrincipalType" = "Anonymous" }
          StringNotEqualsIfExists = { "aws:VpceOrgID" = local.organization_id }
        }
      },
      {
        Sid       = "OnlyServicesActingForTheOrganisation"
        Effect    = "Deny"
        Principal = "*"
        Action    = ["kms:*", "s3:*", "sts:*"]
        Resource  = "*"
        Condition = {
          StringNotEqualsIfExists = { "aws:SourceOrgID" = local.organization_id }
          Null                    = { "aws:SourceAccount" = "false" }
          Bool                    = { "aws:PrincipalIsAWSService" = "true" }
        }
      },
      {
        Sid       = "OnlyTls"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = "*"
        Condition = { BoolIfExists = { "aws:SecureTransport" = "false" } }
      },
    ]
  })
}

resource "aws_organizations_policy" "bootstrap_role_trust" {
  name = "Bootstrap role trust"
  type = "RESOURCE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "OnlyManagementAdministratorsAndDeployOrg"
      Effect    = "Deny"
      Principal = "*"
      Action    = "sts:AssumeRole"
      Resource  = local.bootstrap_role
      Condition = {
        ArnNotLike = {
          "aws:PrincipalArn" = [
            "arn:aws:iam::${local.management_id}:role/aws-reserved/sso.amazonaws.com/*/AWSReservedSSO_${local.management_administrator}_*",
            "arn:aws:iam::${local.management_id}:role/deploy-org",
          ]
        }
        BoolIfExists = { "aws:PrincipalIsAWSService" = "false" }
      }
    }]
  })
}

module "github" {
  source = "../../../modules/github"
}

# The outer limit on GitHub's tokens, inside which each role's trust is its own: only this
# repository's federate, however loose a trust is. A token without the claim is another issuer's,
# which the Identity providers SCP already limits
resource "aws_organizations_policy" "github" {
  name = "GitHub"
  type = "RESOURCE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "OnlyThisRepository"
      Effect    = "Deny"
      Principal = "*"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Resource  = "*"
      Condition = {
        Null            = { "token.actions.githubusercontent.com:repository_id" = "false" }
        StringNotEquals = { "token.actions.githubusercontent.com:repository_id" = module.github.repository_id }
      }
    }]
  })
}

resource "aws_organizations_policy_attachment" "root_rcps" {
  for_each = {
    perimeter            = aws_organizations_policy.perimeter.id
    bootstrap_role_trust = aws_organizations_policy.bootstrap_role_trust.id
    github               = aws_organizations_policy.github.id
  }

  policy_id = each.value
  target_id = local.root_id
}
