stack {
  name  = "platform/aws/baseline"
  id    = "f0ce5618-ca4c-44ee-9c88-e5f140b1acfc"
  after = ["/tofu/security/aws/guardrails"]
  tags  = ["ci"]
  # The account catalogue it reads, which terramate can't follow through fileset()
  watch = [for f in tm_fileset("../accounts", "*.yaml") : "/tofu/platform/aws/accounts/${f}"]
}
