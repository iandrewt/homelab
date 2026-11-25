package main

import rego.v1

# A policy that matched nothing would pass every plan in silence
test_destroying_a_key_is_denied if {
	count(deny) == 1 with input as {"resource_changes": [{
		"address": "aws_kms_key.state",
		"type": "aws_kms_key",
		"change": {"actions": ["delete", "create"], "before": {}, "after": {}},
	}]}
}

test_updating_a_key_is_allowed if {
	count(deny) == 0 with input as {"resource_changes": [{
		"address": "aws_kms_key.state",
		"type": "aws_kms_key",
		"change": {"actions": ["update"], "before": {}, "after": {}},
	}]}
}

test_changing_an_org_role_is_denied if {
	count(deny) == 1 with input as {"resource_changes": [{
		"address": "module.tofu_plan.aws_iam_role.this",
		"type": "aws_iam_role",
		"change": {"actions": ["update"], "before": {"path": "/org/"}, "after": {"path": "/org/"}},
	}]}
}

test_an_org_roles_attachment_is_denied if {
	count(deny) == 1 with input as {"resource_changes": [{
		"address": "aws_iam_role_policy_attachment.tofu_apply[\"network\"]",
		"type": "aws_iam_role_policy_attachment",
		"change": {"actions": ["create"], "before": null, "after": {"role": "tofu-apply"}},
	}]}
}

test_a_teams_role_is_allowed if {
	count(deny) == 0 with input as {"resource_changes": [{
		"address": "aws_iam_role.octodns",
		"type": "aws_iam_role",
		"change": {"actions": ["update"], "before": {"path": "/"}, "after": {"path": "/"}},
	}]}
}
