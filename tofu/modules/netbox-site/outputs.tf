output "vlans" {
  value = {
    for v in data.netbox_vlans.site.vlans : v.name => {
      vid      = v.vid
      vnet     = try(v.custom_fields.vnet, "")
      isolated = contains(v.tag_ids, tonumber(data.netbox_tag.isolate_ports.id))
      subnets = [
        for p in data.netbox_prefixes.vlan[v.name].prefixes : {
          cidr = p.prefix
          gateway = one([
            for ip in local.router_ips_by_family[strcontains(p.prefix, ":") ? "ipv6" : "ipv4"] : ip
            if cidrcontains(p.prefix, ip)
          ])
          vips = [
            for a in local.vips_by_family[strcontains(p.prefix, ":") ? "ipv6" : "ipv4"] : a
            if cidrcontains(p.prefix, a.address)
          ]
        }
      ]
    }
  }
  description = "Each of the site's VLANs by name: its vid, the SDN VNet NetBox names for it (custom field `vnet`, empty if none), whether its ports are isolated (tag Isolate Ports), and its prefixes, each with the router's address in it and the active `vip`-role addresses inside it."
}

output "prefixes" {
  value = {
    for role, r in data.netbox_prefixes.role : role => {
      for family, v6 in { ipv4 = false, ipv6 = true } : family => one([
        for p in r.prefixes : p.prefix
        if strcontains(p.prefix, ":") == v6 && contains(["active", "container"], p.status)
      ])
    }
  }
  description = "Each of `prefix_roles`: the site's active or container prefix with that role, per family."
}
