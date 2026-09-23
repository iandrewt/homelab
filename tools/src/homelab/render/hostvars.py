"""The model as Ansible variables, for `ansible/vars_plugins/netbox.py`.

Plain strings, numbers, lists and dicts only, so a role can index anything the model
guaranteed without a filter in sight.
"""


def host_vars(ipam, hostname: str) -> dict:
    """What a host needs to know about where it is. A host NetBox doesn't know is at no
    site, and gets nothing."""
    site = next((s for s in ipam.sites.values() if hostname in s.hosts), None)
    if site is None:
        return {}
    return {
        "netbox_site": _site(site),
        "netbox_sites": {slug: _site(s) for slug, s in ipam.sites.items()},
        "netbox_zones": [_zone(z) for z in ipam.zones.values()],
    }


def _str(value) -> str | None:
    return None if value is None else str(value)


def _site(site) -> dict:
    return {
        "slug": site.slug,
        "forward_zone": site.forward_zone,
        "router_asn": site.router_asn,
        "gua_prefix": _str(site.gua_prefix),
        "listen_vlans": list(site.listen_vlans),
        "vlans": {
            name: {
                "vid": v.vid,
                "ipv4": str(v.ipv4),
                "ipv6": _str(v.ipv6),
                "gateway_ipv4": str(v.gateway_ipv4),
                "gateway_ipv6": _str(v.gateway_ipv6),
            }
            for name, v in site.vlans.items()
        },
        "pools": {
            purpose: {
                "ipv4": str(p.ipv4),
                "ipv6": _str(p.ipv6),
                "speaker_asn": p.speaker_asn,
                "community": p.community,
            }
            for purpose, p in site.pools.items()
        },
        "anycast": site.anycast and {
            "fqdn": site.anycast.fqdn,
            "ipv4": str(site.anycast.ipv4),
            "ipv6": str(site.anycast.ipv6),
        },
        "resolvers": {
            name: {"fqdn": r.fqdn, "ipv4": str(r.ipv4), "ipv6": str(r.ipv6), "vlan": r.vlan}
            for name, r in site.resolvers.items()
        },
    }


def _zone(zone) -> dict:
    return {"name": zone.name, "nameservers": list(zone.nameservers),
            "soa": zone.soa, "soa_ttl": zone.soa_ttl}
