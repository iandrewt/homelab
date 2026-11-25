stack {
  name = "platform/aws/org"
  id   = "62925652-9abc-4b54-b766-916d7bac4705"
  tags = ["ci"]
  # The account catalogue it reads, which terramate can't follow through fileset()
  watch = [for f in tm_fileset("../accounts", "*.yaml") : "/tofu/platform/aws/accounts/${f}"]
}
