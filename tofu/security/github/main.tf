locals {
  repository = "homelab"

  # GitHub Actions' own app, so a check of the same name from anything else doesn't count
  actions_app = 15368
  # ci.yml's jobs, which run on every pull request
  required_checks = ["tests", "tofu fmt + validate", "kubernetes", "yamllint", "ansible-lint", "homelab.linux on each OS family"]

  # A personal repository's admin: you
  admin = { actor_type = "RepositoryRole", actor_id = 5 }

  release_tags = ["refs/tags/image/**", "refs/tags/extension/**", "refs/tags/fleet/**", "refs/tags/v*"]

  # Each limited to main; pull_request_target runs on the base branch, so a plan is too
  environments = [
    "tofu-plan-org", "tofu-org",
    "tofu-plan-network", "tofu-network",
    "tofu-plan-platform", "tofu-platform",
    "tofu-plan-identity",
  ]
}

# What every commit on main is, whoever pushes it, with no one exempt
resource "github_repository_ruleset" "main" {
  name        = "main"
  repository  = local.repository
  target      = "branch"
  enforcement = "active"

  conditions {
    ref_name {
      include = ["~DEFAULT_BRANCH"]
      exclude = []
    }
  }

  rules {
    deletion                = true
    non_fast_forward        = true
    required_linear_history = true
    required_signatures     = true

    # A fast-forward keeps a pull request's commits, so their checks are already there
    required_status_checks {
      dynamic "required_check" {
        for_each = local.required_checks

        content {
          context        = required_check.value
          integration_id = local.actions_app
        }
      }
    }
  }
}

# Who may push to main at all. Its own ruleset, since an exemption covers every rule in one
resource "github_repository_ruleset" "main_writers" {
  name        = "main writers"
  repository  = local.repository
  target      = "branch"
  enforcement = "active"

  conditions {
    ref_name {
      include = ["~DEFAULT_BRANCH"]
      exclude = []
    }
  }

  rules {
    update = true
  }

  bypass_actors {
    actor_type  = local.admin.actor_type
    actor_id    = local.admin.actor_id
    bypass_mode = "always"
  }
}

# A release is its tag: what it names never moves, and nothing removes it
resource "github_repository_ruleset" "release_tags" {
  name        = "release tags"
  repository  = local.repository
  target      = "tag"
  enforcement = "active"

  conditions {
    ref_name {
      include = local.release_tags
      exclude = []
    }
  }

  rules {
    deletion         = true
    non_fast_forward = true
    update           = true
  }
}

resource "github_repository_ruleset" "release_taggers" {
  name        = "release taggers"
  repository  = local.repository
  target      = "tag"
  enforcement = "active"

  conditions {
    ref_name {
      include = local.release_tags
      exclude = []
    }
  }

  rules {
    creation = true
  }

  bypass_actors {
    actor_type  = local.admin.actor_type
    actor_id    = local.admin.actor_id
    bypass_mode = "always"
  }
}

resource "github_actions_repository_permissions" "this" {
  repository           = local.repository
  enabled              = true
  allowed_actions      = "all"
  sha_pinning_required = true
}

resource "github_workflow_repository_permissions" "this" {
  repository                       = local.repository
  default_workflow_permissions     = "read"
  can_approve_pull_request_reviews = false
}

resource "github_repository_environment" "tofu" {
  for_each = toset(local.environments)

  repository  = local.repository
  environment = each.key

  deployment_branch_policy {
    protected_branches     = false
    custom_branch_policies = true
  }
}

resource "github_repository_environment_deployment_policy" "tofu" {
  for_each = github_repository_environment.tofu

  repository     = local.repository
  environment    = each.value.environment
  branch_pattern = "main"
}
