resource "aws_organizations_policy" "ec2" {
  name = "EC2"
  type = "DECLARATIVE_POLICY_EC2"

  content = jsonencode({
    ec2_attributes = {
      instance_metadata_defaults = {
        http_endpoint               = { "@@assign" = "no_preference" }
        http_put_response_hop_limit = { "@@assign" = "2" }
        http_tokens                 = { "@@assign" = "required" }
        http_tokens_enforced        = { "@@assign" = "enabled" }
        instance_metadata_tags      = { "@@assign" = "no_preference" }
      }
      image_block_public_access    = { state = { "@@assign" = "block_new_sharing" } }
      snapshot_block_public_access = { state = { "@@assign" = "block_all_sharing" } }
      serial_console_access        = { status = { "@@assign" = "disabled" } }
      vpc_block_public_access = {
        internet_gateway_block = {
          mode               = { "@@assign" = "block_bidirectional" }
          exclusions_allowed = { "@@assign" = "enabled" }
        }
      }
    }
  })
}

resource "aws_organizations_policy" "ai_opt_out" {
  name = "AI opt-out"
  type = "AISERVICES_OPT_OUT_POLICY"

  content = jsonencode({
    services = { default = { opt_out_policy = { "@@assign" = "optOut" } } }
  })
}

resource "aws_organizations_policy" "s3" {
  name = "S3"
  type = "S3_POLICY"

  content = jsonencode({
    s3_attributes = { public_access_block_configuration = { "@@assign" = "all" } }
  })
}

resource "aws_organizations_policy_attachment" "root_policies" {
  for_each = {
    ec2        = aws_organizations_policy.ec2.id
    ai_opt_out = aws_organizations_policy.ai_opt_out.id
    s3         = aws_organizations_policy.s3.id
  }

  policy_id = each.value
  target_id = local.root_id
}
