stack {
  name  = "security/aws/detection"
  id    = "126229f4-df77-4ea8-92fa-04218a6b24e1"
  after = ["/tofu/platform/aws/baseline"]
  tags  = ["ci"]
  # The account catalogue it reads, which terramate can't follow through fileset()
  watch = [for f in tm_fileset("../../../platform/aws/accounts", "*.yaml") : "/tofu/platform/aws/accounts/${f}"]
}
