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

data "netbox_tag" "isolate_ports" {
  name = "Isolate Ports"
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

data "netbox_ip_addresses" "vip" {
  filter {
    name  = "role"
    value = "vip"
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

  vips_by_family = {
    for family, v6 in { ipv4 = false, ipv6 = true } : family => [
      for a in data.netbox_ip_addresses.vip.ip_addresses : {
        address  = split("/", a.ip_address)[0]
        dns_name = a.dns_name
      }
      if strcontains(a.ip_address, ":") == v6
    ]
  }
}
