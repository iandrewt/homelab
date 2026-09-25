terraform {
  required_providers {
    netbox = {
      source  = "e-breuninger/netbox"
      version = "~> 5.8"
    }
  }
}

data "netbox_vlans" "site" {
  filter {
    name  = "group"
    value = var.site_slug
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
}
