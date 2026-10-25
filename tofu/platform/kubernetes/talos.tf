module "talos" {
  source = "../../modules/talos-cluster"

  name               = local.name
  contract           = local.cluster.talos_contract
  kubernetes_version = local.cluster.kubernetes_version
  gateway_api        = local.cluster.gateway_api

  api = {
    # Every control plane originates it from `lo`, so the router spreads clients across them
    endpoint  = "https://${local.api.ipv6.dns_name}:6443"
    addresses = { for f, a in local.api : f => a.address }
  }

  classes = {
    for name, c in local.cluster.classes : name => {
      machine_type = c.machine_type
      lifecycle    = c.lifecycle
      talos        = c.talos
      extensions   = c.extensions
      labels       = try(c.labels, {})
    }
  }
  nodes = local.nodes

  network = {
    node = { for f in local.family_order : f => local.subnets[local.node_vlan.vnet][f] }
    pods = { for f in local.family_order : f => local.owned["pods-site"][f] }
  }

  bgp = {
    asn          = local.asn.fabric
    workload_asn = local.asn.workload
    cilium_asn   = local.asn.cilium
    import       = flatten([for purpose in keys(merge(local.blocks, local.pools)) : values(local.owned[purpose])])
  }

  oidc = {
    issuer    = "https://login.microsoftonline.com/${data.azuread_client_config.this.tenant_id}/v2.0"
    client_id = data.azuread_application.this.client_id
    groups    = { for name, group in data.azuread_group.oidc : group.object_id => local.oidc_grants[name] }
  }

  onepassword_vault = data.onepassword_vault.team.uuid
}
