# CI plans these read-only, as Homelab Tofu Plan, and an admin applies them with az login. Their
# plans name people

terramate {
  config {
    run {
      env {
        ARM_TENANT_ID = "5b506f80-57e1-4fda-864e-c2da64b16fd1"
        ARM_CLIENT_ID = "ee5e54cc-9b46-45e7-88a3-77eb045e255a"
        ARM_USE_OIDC  = tm_try(env.GITHUB_ACTIONS, "false")
        ARM_USE_CLI   = tm_try(env.GITHUB_ACTIONS, "false") == "true" ? "false" : "true"
      }
    }
  }
}

import {
  source = "/tofu/ci/private-by-hand.tm.hcl"
}
