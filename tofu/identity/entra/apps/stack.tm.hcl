stack {
  name  = "identity/entra/apps"
  id    = "e3003140-ed28-4346-b96e-b76623d38c9a"
  after = ["/tofu/identity/entra/aws"]
  tags  = ["ci"]
  # The cluster files it reads, which terramate can't follow through fileset()
  watch = tm_concat(
    [for f in tm_fileset("../../../platform/proxmox/clusters", "*.yaml") : "/tofu/platform/proxmox/clusters/${f}"],
    [for f in tm_fileset("../../../platform/kubernetes/clusters", "*.yaml") : "/tofu/platform/kubernetes/clusters/${f}"],
  )
}
