stack {
  name  = "security/aws/logging"
  id    = "ccb7ef00-23bf-4d5e-a64e-146e1c7e5481"
  after = ["/tofu/platform/aws/baseline"]
  tags  = ["ci"]
}
