terraform {
  required_providers {
    netbox = {
      source  = "e-breuninger/netbox"
      version = "~> 5.8"
    }
  }
}

# The same joins as tools/src/homelab/model.py: a site's VLANs are the active ones in the
# group named by its slug, and each gateway is the router's address inside the prefix.

data "netbox_site" "this" {
  slug = var.site_slug
}

data "netbox_vlans" "site" {
  filter {
    name  = "group"
    value = var.site_slug
  }

  filter {
    name  = "status"
    value = "active"
  }
}

data "netbox_tags" "all" {}

data "netbox_prefixes" "trusted" {
  for_each = var.trust_tags

  filter {
    name  = "tag"
    value = each.key
  }

  filter {
    name  = "site_id"
    value = tostring(data.netbox_site.this.id)
  }
}

data "netbox_prefixes" "vlan" {
  for_each = { for v in data.netbox_vlans.site.vlans : v.name => v.id }

  filter {
    name  = "vlan_id"
    value = tostring(each.value)
  }

  filter {
    name  = "status"
    value = "active"
  }
}

data "netbox_device_role" "router" {
  name = "Router Firewall"
}

data "netbox_devices" "routers" {
  filter {
    name  = "site_id"
    value = data.netbox_site.this.id
  }

  filter {
    name  = "role_id"
    value = data.netbox_device_role.router.id
  }
}

data "netbox_ip_addresses" "router" {
  for_each = { for d in data.netbox_devices.routers.devices : d.name => d.device_id }

  filter {
    name  = "device_id"
    value = tostring(each.value)
  }
}

data "netbox_ipam_role" "prefix" {
  for_each = toset(var.prefix_roles)

  name = each.key
}

data "netbox_prefixes" "role" {
  for_each = data.netbox_ipam_role.prefix

  filter {
    name  = "site_id"
    value = tostring(data.netbox_site.this.id)
  }

  filter {
    name  = "role_id"
    value = tostring(each.value.id)
  }
}

# The provider returns object custom fields, like an address's origins, as JSON
data "netbox_ip_addresses" "role" {
  for_each = toset(flatten([for r in data.netbox_prefixes.role : r.prefixes[*].prefix]))

  filter {
    name  = "parent_prefix"
    value = each.key
  }

  filter {
    name  = "status"
    value = "active"
  }
}

locals {
  router_ips = flatten([for r in data.netbox_ip_addresses.router : [for a in r.ip_addresses : split("/", a.ip_address)[0]]])

  # cidrcontains fails rather than answering false across address families
  router_ips_by_family = {
    ipv4 = [for ip in local.router_ips : ip if !strcontains(ip, ":")]
    ipv6 = [for ip in local.router_ips : ip if strcontains(ip, ":")]
  }

  families = { ipv4 = false, ipv6 = true }

  tag_slugs = { for t in data.netbox_tags.all.tags : t.tag_id => t.slug }
  vlan_tags = { for v in data.netbox_vlans.site.vlans : v.name => [for id in v.tag_ids : local.tag_slugs[id]] }

  role_prefixes = {
    for name, r in data.netbox_prefixes.role : data.netbox_ipam_role.prefix[name].slug => [
      for p in r.prefixes : {
        prefix = p.prefix
        family = strcontains(p.prefix, ":") ? "ipv6" : "ipv4"
        origin = try(jsondecode(p.custom_fields["origin"]).asn, null)
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
    if var.origin_asn != null && contains(try([for o in jsondecode(a.custom_fields["origins"]) : o.asn], []), var.origin_asn)
  ]
}
