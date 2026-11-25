# What CI never applies, whatever a plan says: run against `tofu show -json` of every plan
package main

import rego.v1

# Each is a key that signs or encrypts, data under Object Lock, an account, or a URL machines are built with
lasting := {"aws_kms_key", "aws_s3_bucket", "aws_organizations_account", "aws_cloudfront_distribution"}

deny contains msg if {
	some change in input.resource_changes
	change.type in lasting
	"delete" in change.change.actions
	msg := sprintf("%s would be destroyed, which only a person does", [change.address])
}

# The roles CI runs as: CI changing them would change its own reach
org_role_names := {"tofu-plan", "tofu-apply"}

deny contains msg if {
	some change in input.resource_changes
	change.type == "aws_iam_role"
	changes(change)
	org_path(change.change)
	msg := sprintf("%s is an /org/ role, which only a person changes", [change.address])
}

deny contains msg if {
	some change in input.resource_changes
	change.type in {"aws_iam_role_policy", "aws_iam_role_policy_attachment"}
	changes(change)
	org_role(change.change)
	msg := sprintf("%s is an /org/ role's policy, which only a person changes", [change.address])
}

changes(change) if not change.change.actions in {["no-op"], ["read"]}

org_path(c) if c.before.path == "/org/"

org_path(c) if c.after.path == "/org/"

org_role(c) if c.before.role in org_role_names

org_role(c) if c.after.role in org_role_names
