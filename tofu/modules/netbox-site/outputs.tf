output "nodes" {
  value = {
    for node, address in local.node_ipv4 : node => {
      uplinks         = local.uplinks[node]
      vids            = local.vids[node]
      vlan_interfaces = local.vlan_interfaces[node]

      management = {
        vlan_id      = local.node_vlan_id[node]
        ipv4_address = address
        ipv4_gateway = cidrhost(local.node_prefix[node], 1)
        ipv6_address = lookup(local.node_ipv6, node, null)
        ipv6_gateway = try(cidrhost(local.node_prefix6[node], 1), null)
      }
    }
  }
  description = "Each hypervisor's networking: its uplinks, the VLANs its bridge passes, its own tagged interfaces, and the addressing it freezes at install."
}

output "vlans" {
  value       = local.vid
  description = "VLAN name to 802.1Q tag."
}

output "vlan_prefix" {
  value = {
    for name, v in data.netbox_prefixes.vlan :
    name => one([for p in v.prefixes : p.prefix if !strcontains(p.prefix, ":")])
  }
  description = "VLAN name to its IPv4 prefix."
}

output "management_sources" {
  value = flatten([
    for name in var.management_vlans :
    [for p in data.netbox_prefixes.vlan[name].prefixes : p.prefix]
  ])
  description = "Prefixes the cluster firewall admits to the management interface."
}
