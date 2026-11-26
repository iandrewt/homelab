stack {
  name  = "identity/entra/aws"
  id    = "0bbe5c39-912c-4921-a7e0-76658311f824"
  after = ["/tofu/identity/entra/directory"]
  tags  = ["ci"]
}
