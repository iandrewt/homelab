stack {
  name  = "security/aws/guardrails"
  id    = "adab6496-4f07-4f34-80ed-816baaae8f62"
  after = ["/tofu/platform/aws/org"]
  tags  = ["ci"]
  # The account catalogue it reads, which terramate can't follow through fileset()
  watch = [for f in tm_fileset("../../../platform/aws/accounts", "*.yaml") : "/tofu/platform/aws/accounts/${f}"]
}
