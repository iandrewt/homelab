# The gallery app. Its SAML certificate and SCIM credentials are set by hand.
data "azuread_service_principal" "aws" {
  client_id = "63ae04d6-e810-4d36-90d1-45d95b2b8e77"
}

module "groups" {
  source = "../../../modules/entra-groups"

  names = keys(module.tenancy.groups)
}

data "azuread_application" "directory" {
  display_name = "Homelab Directory"
}

data "msgraph_resource" "extensions" {
  url                    = "applications/${data.azuread_application.directory.object_id}/extensionProperties"
  response_export_values = { names = "value[].name" }
}

locals {
  aws_sp        = data.azuread_service_principal.aws.object_id
  aws_extension = one([for n in data.msgraph_resource.extensions.output.names : n if endswith(n, "_aws")])

  # platform/aws/identity assigns every one of them
  aws_users = toset(flatten(values(module.groups.users)))
}

resource "azuread_app_role_assignment" "aws" {
  for_each = local.aws_users

  app_role_id         = one([for r in data.azuread_service_principal.aws.app_roles : r.id if r.display_name == "User"])
  principal_object_id = each.key
  resource_object_id  = local.aws_sp
}

resource "msgraph_update_resource" "aws_groups" {
  for_each = module.groups.object_ids

  url                   = "groups/${each.value}"
  body                  = { (local.aws_extension) = "true" }
  read_query_parameters = { "$select" = [local.aws_extension] }
}

resource "msgraph_update_resource" "aws_users" {
  for_each = local.aws_users

  url                   = "users/${each.key}"
  body                  = { (local.aws_extension) = "true" }
  read_query_parameters = { "$select" = [local.aws_extension] }
}

# Destroying an update resource writes nothing, so leavers are found and cleared
data "msgraph_resource" "aws_marked" {
  url = "users"
  query_parameters = {
    "$filter" = ["${local.aws_extension} eq 'true'"]
    "$select" = ["id"]
  }
  response_export_values = { ids = "value[].id" }
}

resource "msgraph_update_resource" "aws_unmarked" {
  for_each = setsubtract(toset(data.msgraph_resource.aws_marked.output.ids), local.aws_users)

  url                   = "users/${each.key}"
  body                  = { (local.aws_extension) = "false" }
  read_query_parameters = { "$select" = [local.aws_extension] }
}

data "msgraph_resource" "aws_jobs" {
  url = "servicePrincipals/${local.aws_sp}/synchronization/jobs"
  response_export_values = {
    ids    = "value[].id"
    codes  = "value[].status.code"
    states = "value[].status.lastExecution.state"
  }
}

# The SCIM token's expiry is visible nowhere, so this is how it shows
check "aws_provisioning" {
  assert {
    condition = alltrue([
      for i, code in data.msgraph_resource.aws_jobs.output.codes :
      code == "Active" && data.msgraph_resource.aws_jobs.output.states[i] == "Succeeded"
    ])
    error_message = "AWS IAM Identity Center's provisioning is ${join(", ", data.msgraph_resource.aws_jobs.output.codes)}: if its token has expired, make a new one in Identity Center and save it in Entra."
  }
}

check "aws_signing_certificate" {
  data "msgraph_resource" "aws_signing" {
    url                    = "servicePrincipals/${local.aws_sp}"
    query_parameters       = { "$select" = ["keyCredentials"] }
    response_export_values = { ends = "keyCredentials[?usage == 'Verify'].endDateTime" }
  }

  assert {
    condition     = alltrue([for end in data.msgraph_resource.aws_signing.output.ends : timecmp(end, timeadd(plantimestamp(), "1440h")) > 0])
    error_message = "AWS IAM Identity Center's SAML certificate expires within 60 days: make a new one in Entra and upload it to Identity Center."
  }
}

data "msgraph_resource" "aws_schema" {
  url                    = "servicePrincipals/${local.aws_sp}/synchronization/jobs/${one(data.msgraph_resource.aws_jobs.output.ids)}/schema"
  response_export_values = { all = "@" }
}

locals {
  # PUT whole: what was read, with the scope, the mappings and the extension replaced
  aws_schema = data.msgraph_resource.aws_schema.output.all

  aws_extension_attribute = {
    anchor            = false
    apiExpressions    = []
    caseExact         = false
    defaultValue      = null
    flowNullValues    = false
    metadata          = []
    multivalued       = false
    mutability        = "ReadWrite"
    name              = local.aws_extension
    referencedObjects = []
    required          = false
    type              = "String"
  }

  aws_scope = {
    User = [{
      name = "LimitUsers"
      clauses = [
        { operatorName = "EQUALS", sourceOperandName = local.aws_extension, targetOperand = { values = ["true"] } },
        # Members, not guests: userType can't be a scoping attribute
        { operatorName = "NOT REGEX MATCH", sourceOperandName = "userPrincipalName", targetOperand = { values = ["#EXT#"] } },
      ]
    }]
    Group = [{
      name    = "LimitGroups"
      clauses = [{ operatorName = "EQUALS", sourceOperandName = local.aws_extension, targetOperand = { values = ["true"] } }]
    }]
  }

  # Nothing in AWS reads the rest, and it would be personal data copied out
  aws_user_attributes = ["userName", "active", "displayName", "name.givenName", "name.familyName", "emails[type eq \"work\"].value", "externalId"]
}

# A new scope starts a full sync, which disables whoever lacks the attribute
resource "msgraph_update_resource" "aws_schema" {
  url           = data.msgraph_resource.aws_schema.url
  update_method = "PUT"
  depends_on    = [msgraph_update_resource.aws_groups, msgraph_update_resource.aws_users]
  body = {
    directories = [
      for d in local.aws_schema.directories : merge(d, {
        objects = [
          for o in d.objects : merge(o, {
            attributes = concat(
              [for a in o.attributes : a if a.name != local.aws_extension],
              d.name == "Microsoft Entra ID" && contains(["User", "Group"], o.name) ? [local.aws_extension_attribute] : [],
            )
          })
        ]
      })
    ]
    synchronizationRules = [
      for r in local.aws_schema.synchronizationRules : merge(r, {
        objectMappings = [
          for m in r.objectMappings : merge(m, {
            scope = merge(m.scope, { groups = try(local.aws_scope[m.sourceObjectName], m.scope.groups) })
            attributeMappings = [
              for a in m.attributeMappings : a
              if m.sourceObjectName != "User" || contains(local.aws_user_attributes, a.targetAttributeName)
            ]
          })
        ]
      })
    ]
  }
}

locals {
  # As the gallery app ships them
  aws_user_claims = { givenname = "givenname", surname = "surname", emailaddress = "mail", name = "userprincipalname" }
}

# Team membership as a session tag, which TofuState's trust reads
resource "msgraph_update_resource" "aws_claims" {
  url           = "servicePrincipals/${local.aws_sp}/claimsPolicy"
  api_version   = "beta"
  update_method = "PUT"
  body = {
    includeBasicClaimSet         = true
    includeApplicationIdInIssuer = false
    claims = concat(
      [{
        "@odata.type" = "#microsoft.graph.samlNameIdClaim"
        nameIdFormat  = "emailAddress"
        configurations = [{
          attribute       = { "@odata.type" = "#microsoft.graph.sourcedAttribute", id = "userprincipalname", isExtensionAttribute = false, source = "user" }
          condition       = null
          transformations = []
        }]
      }],
      [
        for claim, source in local.aws_user_claims : {
          "@odata.type" = "#microsoft.graph.customClaim"
          name          = claim
          namespace     = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims"
          tokenFormat   = ["saml"]
          configurations = [{
            attribute       = { "@odata.type" = "#microsoft.graph.sourcedAttribute", id = source, isExtensionAttribute = false, source = "user" }
            condition       = null
            transformations = []
          }]
        }
      ],
      [
        for name, team in module.tenancy.teams : {
          "@odata.type" = "#microsoft.graph.customClaim"
          name          = "AccessControl:team-${name}"
          namespace     = "https://aws.amazon.com/SAML/Attributes"
          tokenFormat   = ["saml"]
          configurations = [{
            attribute       = { "@odata.type" = "#microsoft.graph.valueBasedAttribute", value = "true" }
            condition       = { "@odata.type" = "#microsoft.graph.customClaimCondition", memberOf = ["oid:${module.groups.object_ids[team.title]}"], userType = "members" }
            transformations = []
          }]
        }
      ],
    )
  }
}
