# Each OpenTofu layer is a stack: what it runs after, and its tags, are its own stack.tm.hcl's
terramate {
  required_version = "~> 0.17.4"

  config {
    git {
      default_branch = "main"
    }

    experiments = ["scripts"]
  }
}
