locals {
  family_order = ["ipv6", "ipv4"]

  # Each pool a cluster can originate, by purpose. `role` is its name in NetBox, since the
  # provider can look a role up by nothing else. A cluster gets a pool by NetBox giving it
  # one: the pools component makes what it's given.
  pools = {
    pods-default  = { role = "Pods Default", kind = "pod", name = "default", mask4 = 28 }
    pods-storage  = { role = "Pods Storage", kind = "pod", name = "storage", mask4 = 30 }
    pods-internet = { role = "Pods Internet", kind = "pod", name = "internet", mask4 = 30 }
    lb-site       = { role = "Load Balancer Site", kind = "lb", name = "lb-site", exposure = "site" }
    lb-home       = { role = "Load Balancer Home", kind = "lb", name = "lb-home", exposure = "home" }
    lb-internet   = { role = "Load Balancer Internet", kind = "lb", name = "lb-internet", exposure = "internet" }
  }
  # The router accepts routes inside these: the site's block holds its pod pools
  blocks = { pods-site = "Pods Site" }
  roles  = merge({ for p, pool in local.pools : p => pool.role }, local.blocks, { kubernetes-api = "Kubernetes API" })

  asn = { fabric = local.cluster.asn, workload = local.cluster.asn + 1, cilium = local.cluster.asn + 2 }
}

module "netbox" {
  source = "../../modules/netbox-site"

  site_slug      = local.cluster.site
  origin_asn     = local.cluster.asn
  prefix_roles   = values(local.roles)
  audience_roles = ["kubernetes-api"]
}

locals {
  node_vlan    = one([for name, v in module.netbox.vlans : merge(v, { name = name }) if v.cluster == local.name])
  vlan_by_vnet = { for name, v in module.netbox.vlans : v.vnet => v if v.vnet != "" }

  subnets = {
    for vnet, v in merge({ (local.node_vlan.vnet) = local.node_vlan }, { for vnet in try(local.cluster.vnets, []) : vnet => local.vlan_by_vnet[vnet] }) :
    vnet => { for s in v.subnets : (strcontains(s.cidr, ":") ? "ipv6" : "ipv4") => s }
  }

  owned = {
    for purpose in keys(local.roles) : purpose => {
      for f in local.family_order : f => one(module.netbox.owned[purpose][f]) if length(module.netbox.owned[purpose][f]) > 0
    }
  }

  api = { for f in local.family_order : f => one(module.netbox.assigned["kubernetes-api"][f]) }

}

data "azuread_client_config" "this" {}

# platform/entra/apps's
data "azuread_application" "this" {
  display_name = "Kubernetes ${local.name}"
}

locals {
  # Every group, Entra name to RBAC name: who someone is is the org's, what they may do the
  # cluster's RBAC, so a grant never touches a machine config
  oidc_grants = merge(
    { Administrators = "administrators", Security = "security" },
    { for team, t in module.tenancy.teams : t.title => team },
  )
}

data "azuread_group" "oidc" {
  for_each = local.oidc_grants

  display_name     = each.key
  security_enabled = true
}
