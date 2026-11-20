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

data "netbox_locations" "all" {
  limit = 10000
}

data "netbox_virtual_machines" "all" {}

data "netbox_ip_addresses" "primaries" {
  count = length(flatten(local.tagged_hosts[*].addresses)) > 0 ? 1 : 0

  filters = [for id in flatten(local.tagged_hosts[*].addresses) : { name = "id", value = tostring(id) }]
}

locals {
  location_sites = { for l in data.netbox_locations.all.locations : l.id => l.site_id }
  vlans_by_id    = { for v in data.netbox_vlans.all.vlans : v.id => v }

  prefixes_all = [
    for p in data.netbox_prefixes.all.prefixes : {
      prefix = p.prefix
      family = strcontains(p.prefix, ":") ? "ipv6" : "ipv4"
      length = tonumber(split("/", p.prefix)[1])
      # An AZ's prefix is scoped to its Location, which is at the site
      site = coalesce(p.site_id, try(local.location_sites[p.location_id], null), 0)
      zone = try(tonumber(p.custom_fields.zone), local.vlans_by_id[p.vlan_id].role_id, null)
      tags = setunion(coalesce(p.tags, []), coalesce(try(local.vlans_by_id[p.vlan_id].tags, null), []))
      audience = {
        zones = try(jsondecode(p.custom_fields.audience), [])
        tags  = [for id in try(jsondecode(p.custom_fields.audience_tags), []) : local.tag_slugs[id]]
        sites = try(jsondecode(p.custom_fields.audience_sites), [])
      }
    }
    if contains(["active", "container"], p.status)
  ]

  # A host's tag covers its primary addresses, the ones it's reached from
  tagged_hosts = [
    for h in concat(
      [for d in data.netbox_devices.all.devices : { tags = d.tags, ids = [d.primary_ip4_id, d.primary_ip6_id] }],
      [for v in data.netbox_virtual_machines.all.virtual_machines : { tags = v.tags, ids = [v.primary_ip4_id, v.primary_ip6_id] }],
    ) : { tags = h.tags, addresses = compact(h.ids) }
    if h.tags != null
  ]
  primaries = { for a in try(data.netbox_ip_addresses.primaries[0].ip_addresses, []) : a.id => a.ip_address }
  host_sources = flatten([
    for h in local.tagged_hosts : [
      for id in h.addresses : {
        prefix = "${split("/", local.primaries[id])[0]}/${strcontains(local.primaries[id], ":") ? 128 : 32}"
        family = strcontains(local.primaries[id], ":") ? "ipv6" : "ipv4"
        tags   = h.tags
      }
    ]
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
