output "vlans" {
  value = {
    for v in data.netbox_vlans.site.vlans : v.name => {
      vid     = v.vid
      vnet    = try(v.custom_fields.vnet, "")
      cluster = try(jsondecode(v.custom_fields.cluster).name, null)
      tags    = local.vlan_tags[v.name]
      subnets = [
        for p in data.netbox_prefixes.vlan[v.name].prefixes : {
          cidr = p.prefix
          # A VLAN-only network has no router interface, so none
          gateway = one([
            for ip in local.router_ips_by_family[strcontains(p.prefix, ":") ? "ipv6" : "ipv4"] : ip
            if cidrcontains(p.prefix, ip)
          ])
        }
      ]
    }
  }
  description = "The site's VLANs by name: vid, `vnet` and `cluster`, tag slugs, and a prefix per family with the router's address in it (null on a VLAN-only network)."
}

output "prefixes" {
  value = {
    for role, prefixes in local.role_prefixes : role => {
      for family in keys(local.families) : family => [for p in prefixes : p.prefix if p.family == family]
    }
  }
  description = "Per role slug and family: the site's active or container prefixes with that role."
}

output "owned" {
  value = {
    for role, prefixes in local.role_prefixes : role => {
      for family in keys(local.families) : family => [
        for p in prefixes : p.prefix if p.family == family && var.origin_asn != null && p.origin == var.origin_asn
      ]
    }
  }
  description = "Per role slug and family: those of them `origin_asn` originates."
}

output "assigned" {
  value = {
    for role, prefixes in local.role_prefixes : role => {
      for family in keys(local.families) : family => [
        for a in local.owned_addresses : a
        if a.family == family && anytrue([for p in prefixes : cidrcontains(p.prefix, a.address) if p.family == family])
      ]
    }
  }
  description = "Per role slug and family: the addresses inside them `origin_asn` is among the origins of, with each `dns_name`."
}

output "trusted" {
  value = {
    for tag in var.trust_tags : tag => sort(distinct(concat(
      data.netbox_prefixes.trusted[tag].prefixes[*].prefix,
      flatten([for name, ids in data.netbox_prefixes.vlan : ids.prefixes[*].prefix if contains(local.vlan_tags[name], tag)]),
      [for h in local.host_sources : h.prefix if contains(h.tags, tag)],
    )))
  }
  description = "Per trust tag: the site's prefixes carrying it, a VLAN's or a sub-pool's, and the addresses of the hosts carrying it, anywhere. A firewall names the tag, never an address."
}

output "audiences" {
  value = {
    for name, found in local.audience_sources : name => {
      for family in keys(local.families) : family => sort(distinct([for s in found : s.prefix if s.family == family]))
    }
  }
  description = "Per `destinations` name and `audience_roles` slug, and per family: the sources its audience admits. `internet` is no source of ours, so it never appears."
}
