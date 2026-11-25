stack {
  name  = "identity/aws/identity-center"
  id    = "bb2cc41b-f85a-4691-ad06-c8a24b2e4481"
  after = ["/tofu/security/aws/guardrails"]
  tags  = ["ci"]
  # The account catalogue it reads, which terramate can't follow through fileset()
  watch = [for f in tm_fileset("../../../platform/aws/accounts", "*.yaml") : "/tofu/platform/aws/accounts/${f}"]
}
