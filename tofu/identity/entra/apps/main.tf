locals {
  proxmox_clusters = {
    for f in fileset("${path.module}/../../../platform/proxmox/clusters", "*.yaml") :
    trimsuffix(f, ".yaml") => yamldecode(file("${path.module}/../../../platform/proxmox/clusters/${f}"))
  }
  kubernetes_clusters = {
    for f in fileset("${path.module}/../../../platform/kubernetes/clusters", "*.yaml") :
    trimsuffix(f, ".yaml") => yamldecode(file("${path.module}/../../../platform/kubernetes/clusters/${f}"))
  }

  # One per instance, since the client ID is the audience. Owned here, so a rebuild keeps it.
  apps = merge(
    {
      for name, cluster in local.proxmox_clusters : "Proxmox ${name}" => {
        logo = "${path.module}/logos/proxmox.png"
        # The login page sends its origin exactly: no path, no trailing slash. A node's own name signs in nobody
        redirect_uris = [regex("^https://[^/]+", cluster.endpoint)]
        groups = concat(["Administrators", "Security"], [for t in keys(cluster.tenants) : module.tenancy.teams[t].title])
      }
    },
    {
      for name, cluster in local.kubernetes_clusters : "Kubernetes ${name}" => {
        logo          = "${path.module}/logos/kubernetes.png"
        redirect_uris = ["http://localhost:8000"]
        groups        = concat(["Administrators", "Security"], [for t in keys(try(cluster.tenants, {})) : module.tenancy.teams[t].title])
      }
    },
  )
}

module "groups" {
  source = "../../../modules/entra-groups"

  names = keys(module.tenancy.groups)
}

module "app" {
  source   = "../../../modules/entra-app"
  for_each = local.apps

  display_name  = each.key
  logo          = each.value.logo
  redirect_uris = each.value.redirect_uris
  users         = toset(flatten([for g in each.value.groups : module.groups.users[g]]))
}
