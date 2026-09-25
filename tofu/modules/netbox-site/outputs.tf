output "vnets" {
  value = {
    for v in data.netbox_vlans.site.vlans : tostring(v.vid) => {
      id       = v.custom_fields.vnet
      name     = v.name
      isolated = contains(v.tag_ids, tonumber(data.netbox_tag.isolate_ports.id))
      prefixes = [for p in data.netbox_prefixes.vlan[v.name].prefixes : p.prefix]
    }
    if try(v.custom_fields.vnet, "") != ""
  }
  description = "802.1Q tag to the SDN VNet NetBox names for it (the VLAN's `vnet` custom field), whether its ports are isolated (tag Isolate Ports), and its prefixes."
}

output "vlan_prefixes" {
  value = {
    for name, v in data.netbox_prefixes.vlan : name => [for p in v.prefixes : p.prefix]
  }
  description = "VLAN name to its prefixes, one per family."
}

output "management_sources" {
  value = flatten([
    for name in var.management_vlans :
    [for p in data.netbox_prefixes.vlan[name].prefixes : p.prefix]
  ])
  description = "Prefixes the cluster firewall admits to the management interface."
}
