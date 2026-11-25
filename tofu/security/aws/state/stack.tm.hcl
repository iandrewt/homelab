stack {
  name  = "security/aws/state"
  id    = "abbfa165-eb3e-4724-b2ab-d2239e9d4c03"
  after = ["/tofu/platform/aws/baseline"]
  tags  = ["ci"]
}
