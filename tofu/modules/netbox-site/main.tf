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

locals {
  router_ips = flatten([for r in data.netbox_ip_addresses.router : [for a in r.ip_addresses : split("/", a.ip_address)[0]]])

  # cidrcontains fails rather than answering false across address families
  router_ips_by_family = {
    ipv4 = [for ip in local.router_ips : ip if !strcontains(ip, ":")]
    ipv6 = [for ip in local.router_ips : ip if strcontains(ip, ":")]
  }
}
