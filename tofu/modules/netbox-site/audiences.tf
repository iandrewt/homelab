# The same expansion as model.sources: a zone is a Role, the prefixes stating it and the
# prefixes of VLANs having it, at the audience's sites; a trust tag is the prefixes, VLANs and
# hosts carrying it. The provider gives a VLAN's Role by ID, so zones compare by ID.

data "netbox_prefixes" "all" {
  limit = 10000
}

data "netbox_vlans" "all" {
  limit = 10000
}

data "netbox_devices" "all" {}

data "netbox_virtual_machines" "all" {}

locals {
  # Prefixes and devices name their tags; VLANs and VMs give IDs
  tag_by_name = { for t in data.netbox_tags.all.tags : t.name => t.slug }

  vlan_zones      = { for v in data.netbox_vlans.all.vlans : v.id => v.role }
  vlan_tags_by_id = { for v in data.netbox_vlans.all.vlans : v.id => [for id in v.tag_ids : local.tag_slugs[id]] }

  prefixes_all = [
    for p in data.netbox_prefixes.all.prefixes : {
      prefix = p.prefix
      family = strcontains(p.prefix, ":") ? "ipv6" : "ipv4"
      length = tonumber(split("/", p.prefix)[1])
      site   = p.site_id
      zone   = try(jsondecode(p.custom_fields.zone).id, lookup(local.vlan_zones, p.vlan_id, null))
      tags   = setunion([for name in p.tags : local.tag_by_name[name]], lookup(local.vlan_tags_by_id, p.vlan_id, []))
      audience = {
        zones = try([for z in jsondecode(p.custom_fields.audience) : z.id], [])
        tags  = try([for t in jsondecode(p.custom_fields.audience_tags) : t.slug], [])
        sites = try([for s in jsondecode(p.custom_fields.audience_sites) : s.id], [])
      }
    }
    if contains(["active", "container"], p.status)
  ]

  # A host's tag covers its primary addresses, the ones it's reached from
  tagged_primaries = concat(
    [for d in data.netbox_devices.all.devices : { tags = [for name in d.tags : local.tag_by_name[name]], addresses = [d.primary_ipv4, d.primary_ipv6] }],
    [for v in data.netbox_virtual_machines.all.vms : { tags = [for id in v.tag_ids : local.tag_slugs[id]], addresses = [v.primary_ip4, v.primary_ip6] }],
  )
  host_sources = flatten([
    for h in local.tagged_primaries : [
      for a in h.addresses : {
        prefix = "${split("/", a)[0]}/${strcontains(a, ":") ? 128 : 32}"
        family = strcontains(a, ":") ? "ipv6" : "ipv4"
        tags   = h.tags
      }
      if a != null && a != ""
    ]
    if length(h.tags) > 0
  ])

  destination_holders = {
    for name, address in var.destinations : name => [
      for p in local.prefixes_all : p
      if p.family == (strcontains(address, ":") ? "ipv6" : "ipv4") && cidrcontains(p.prefix, address)
      && length(p.audience.zones) + length(p.audience.tags) > 0
    ]
  }

  role_holders = {
    for role in var.audience_roles : role => [
      for p in local.prefixes_all : p
      if contains(local.role_prefixes[role][*].prefix, p.prefix) && length(p.audience.zones) + length(p.audience.tags) > 0
    ]
  }

  # A role's prefixes are one pool per family, so they state one audience between them
  destination_audiences = merge(
    { for name, holders in local.destination_holders : name => one([for p in holders : p.audience if p.length == max(holders[*].length...)]) },
    { for role, holders in local.role_holders : role => one(distinct(holders[*].audience)) },
  )

  audience_sources = {
    for name, a in local.destination_audiences : name => distinct(concat(
      [
        for p in local.prefixes_all : { prefix = p.prefix, family = p.family }
        if contains(a.zones, coalesce(p.zone, -1)) && (length(a.sites) == 0 || p.site == 0 || contains(a.sites, p.site))
      ],
      [for p in local.prefixes_all : { prefix = p.prefix, family = p.family } if length(setintersection(p.tags, a.tags)) > 0],
      [for h in local.host_sources : { prefix = h.prefix, family = h.family } if length(setintersection(h.tags, a.tags)) > 0],
    ))
  }
}
