terraform {
  required_providers {
    netbox = {
      source  = "e-breuninger/netbox"
      version = "~> 5.8"
    }
  }
}

data "netbox_site" "this" {
  slug = var.site_slug
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

data "netbox_devices" "nodes" {
  filter {
    name  = "site_id"
    value = tostring(data.netbox_site.this.site_id)
  }
}

data "netbox_device_interfaces" "node" {
  for_each = local.node_device

  filter {
    name  = "device_id"
    value = tostring(each.value.device_id)
  }
}

# An address doesn't record which interface holds it, so each interface is asked instead.
data "netbox_ip_addresses" "interface" {
  for_each = local.interface_id

  filter {
    name  = "interface_id"
    value = tostring(each.value)
  }
}

data "netbox_prefixes" "holding" {
  for_each = local.node_ipv4

  filter {
    name  = "contains"
    value = split("/", each.value)[0]
  }
}

data "netbox_prefixes" "holding6" {
  for_each = { for node, address in local.node_ipv6 : node => address if address != null }

  filter {
    name  = "contains"
    value = split("/", each.value)[0]
  }
}

data "netbox_prefixes" "vlan" {
  for_each = local.vlan_id

  filter {
    name  = "vlan_id"
    value = tostring(each.value)
  }
}

locals {
  vid      = { for v in data.netbox_vlans.site.vlans : v.name => v.vid }
  vlan_id  = { for v in data.netbox_vlans.site.vlans : v.name => v.id }
  vlan_vid = { for v in data.netbox_vlans.site.vlans : tostring(v.id) => v.vid }

  node_device = {
    for d in data.netbox_devices.nodes.devices : d.name => d if contains(var.nodes, d.name)
  }

  node_interfaces = {
    for node, result in data.netbox_device_interfaces.node :
    node => { for i in result.interfaces : i.name => i }
  }

  interface_id = merge([
    for node, interfaces in local.node_interfaces :
    { for name, i in interfaces : "${node}/${name}" => i.id }
  ]...)

  interface_address = {
    for key, result in data.netbox_ip_addresses.interface : key => {
      ipv4 = one([for a in result.ip_addresses : a.ip_address if a.address_family == "IPv4"])
      ipv6 = one([for a in result.ip_addresses : a.ip_address if a.address_family == "IPv6"])
    }
  }

  # Whichever interface holds the device's primary address is the one the default route
  # belongs on. Every other addressed interface is on-link only.
  management_interface = {
    for node, device in local.node_device : node => one([
      for name, _ in local.node_interfaces[node] : name
      if try(split("/", local.interface_address["${node}/${name}"].ipv4)[0], null) == split("/", device.primary_ipv4)[0]
    ])
  }

  node_ipv4 = {
    for node, name in local.management_interface :
    node => local.interface_address["${node}/${name}"].ipv4 if name != null
  }

  node_ipv6 = {
    for node, name in local.management_interface :
    node => local.interface_address["${node}/${name}"].ipv6 if name != null
  }

  uplinks = {
    for node, interfaces in local.node_interfaces : node => sort([
      for name, i in interfaces : name if i.lag_device_interface_id == try(interfaces[var.bond].id, 0)
    ])
  }

  # The bridge's tagged VLANs are the trunk: what it passes, and what becomes an SDN VNet.
  vids = {
    for node, interfaces in local.node_interfaces : node => [
      for padded in sort([for v in try(interfaces[var.bridge].tagged_vlans, []) : format("%04d", v.vid)]) :
      tonumber(padded)
    ]
  }

  vlan_interfaces = {
    for node, interfaces in local.node_interfaces : node => {
      for name, i in interfaces : tostring(one(i.untagged_vlan).vid) => {
        ipv4_address = local.interface_address["${node}/${name}"].ipv4
        ipv6_address = local.interface_address["${node}/${name}"].ipv6
      }
      if startswith(name, "${var.bridge}.") && anytrue([
        local.interface_address["${node}/${name}"].ipv4 != null,
        local.interface_address["${node}/${name}"].ipv6 != null,
      ])
    }
  }

  # The most specific prefix wins
  node_prefix = {
    for node, result in data.netbox_prefixes.holding :
    node => split(" ", reverse(sort([
      for p in result.prefixes : format("%02d %s", tonumber(split("/", p.prefix)[1]), p.prefix)
    ]))[0])[1]
  }

  node_prefix6 = {
    for node, result in data.netbox_prefixes.holding6 :
    node => split(" ", reverse(sort([
      for p in result.prefixes : format("%03d %s", tonumber(split("/", p.prefix)[1]), p.prefix)
    ]))[0])[1]
  }

  node_vlan_id = {
    for n, _ in local.node_ipv4 :
    n => lookup(
      local.vlan_vid,
      tostring(one([for p in data.netbox_prefixes.holding[n].prefixes : p.vlan_id if p.prefix == local.node_prefix[n]])),
      null
    )
  }
}
