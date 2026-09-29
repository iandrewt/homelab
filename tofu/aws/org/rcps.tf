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
          "sts:SetContext",
        ]
        Resource = "*"
        Condition = {
          StringNotEqualsIfExists = { "aws:PrincipalOrgID" = aws_organizations_organization.this.id }
          BoolIfExists            = { "aws:PrincipalIsAWSService" = "false" }
        }
      },
      {
        Sid       = "OnlyServicesActingForTheOrganisation"
        Effect    = "Deny"
        Principal = "*"
        Action    = ["kms:*", "s3:*", "sts:*"]
        Resource  = "*"
        Condition = {
          StringNotEqualsIfExists = { "aws:SourceOrgID" = aws_organizations_organization.this.id }
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
      Sid       = "OnlyManagementAdministrators"
      Effect    = "Deny"
      Principal = "*"
      Action    = "sts:AssumeRole"
      Resource  = local.bootstrap_role
      Condition = {
        ArnNotLike = {
          "aws:PrincipalArn" = "arn:aws:iam::${local.management_id}:role/aws-reserved/sso.amazonaws.com/*/AWSReservedSSO_${aws_ssoadmin_permission_set.management_administrator.name}_*"
        }
      }
    }]
  })
}

resource "aws_organizations_policy_attachment" "root_rcps" {
  for_each = {
    perimeter            = aws_organizations_policy.perimeter.id
    bootstrap_role_trust = aws_organizations_policy.bootstrap_role_trust.id
  }

  policy_id = each.value
  target_id = local.root_id
}
