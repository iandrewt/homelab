terraform {
  required_providers {
    netbox = {
      source  = "e-breuninger/netbox"
      version = "~> 6.0"
    }
  }
}

# The same joins as tools/src/homelab/model.py: a site's VLANs are the active ones in the
# group named by its slug, and each gateway is the router's address inside the prefix.

data "netbox_site" "this" {
  slug = var.site_slug
}

data "netbox_vlans" "site" {
  filters = [
    { name = "group", value = var.site_slug },
    { name = "status", value = "active" },
  ]
}

data "netbox_tags" "all" {}

# Object custom fields hold the related object's ID
data "netbox_asns" "all" {
  limit = 10000
}

data "netbox_clusters" "all" {}

data "netbox_prefixes" "vlan" {
  for_each = { for v in data.netbox_vlans.site.vlans : v.name => v.id }

  filters = [
    { name = "vlan_id", value = tostring(each.value) },
    { name = "status", value = "active" },
  ]
}

data "netbox_device_role" "router" {
  name = "Router Firewall"
}

data "netbox_devices" "routers" {
  filters = [
    { name = "site_id", value = tostring(data.netbox_site.this.id) },
    { name = "role_id", value = tostring(data.netbox_device_role.router.id) },
  ]
}

data "netbox_ip_addresses" "router" {
  for_each = { for d in data.netbox_devices.routers.devices : d.name => d.id }

  filters = [{ name = "device_id", value = tostring(each.value) }]
}

data "netbox_prefixes" "role" {
  for_each = toset(var.prefix_roles)

  filters = [
    { name = "site_id", value = tostring(data.netbox_site.this.id) },
    { name = "role", value = each.key },
  ]
}

data "netbox_ip_addresses" "role" {
  for_each = toset(flatten([for r in data.netbox_prefixes.role : r.prefixes[*].prefix]))

  filters = [
    { name = "parent", value = each.key },
    { name = "status", value = "active" },
  ]
}

locals {
  router_ips = flatten([for r in data.netbox_ip_addresses.router : [for a in r.ip_addresses : split("/", a.ip_address)[0]]])

  # cidrcontains fails rather than answering false across address families
  router_ips_by_family = {
    ipv4 = [for ip in local.router_ips : ip if !strcontains(ip, ":")]
    ipv6 = [for ip in local.router_ips : ip if strcontains(ip, ":")]
  }

  families = { ipv4 = false, ipv6 = true }

  # A sub-pool naming its VLAN too is inside the VLAN's network, which stays; two apart fail here
  vlan_subnets = {
    for name, found in data.netbox_prefixes.vlan : name => [
      for family in keys(local.families) : one([
        for p in found.prefixes : p.prefix
        if(strcontains(p.prefix, ":") ? "ipv6" : "ipv4") == family && !anytrue([
          for q in found.prefixes : q.prefix != p.prefix && strcontains(q.prefix, ":") == strcontains(p.prefix, ":") && cidrcontains(q.prefix, p.prefix)
        ])
      ])
      if anytrue([for p in found.prefixes : (strcontains(p.prefix, ":") ? "ipv6" : "ipv4") == family])
    ]
  }

  tag_slugs = { for t in data.netbox_tags.all.tags : t.id => t.slug }
  asns      = { for a in data.netbox_asns.all.asns : a.id => a.asn }
  clusters  = { for c in data.netbox_clusters.all.clusters : c.id => c.name }

  role_prefixes = {
    for role, r in data.netbox_prefixes.role : role => [
      for p in r.prefixes : {
        prefix = p.prefix
        family = strcontains(p.prefix, ":") ? "ipv6" : "ipv4"
        origin = try(local.asns[p.custom_fields["origin"]], null)
      }
      if contains(["active", "container"], p.status)
    ]
  }

  owned_addresses = [
    for a in distinct(flatten([for r in data.netbox_ip_addresses.role : r.ip_addresses])) : {
      address  = split("/", a.ip_address)[0]
      family   = strcontains(a.ip_address, ":") ? "ipv6" : "ipv4"
      dns_name = a.dns_name
    }
    if var.origin_asn != null && contains([for id in try(jsondecode(a.custom_fields["origins"]), []) : local.asns[id]], var.origin_asn)
  ]
}
