import pytest
from homelab import netbox

SITE = "one"


def _site_ref(slug: str) -> dict:
    return {"name": f"{slug.title()}ville", "slug": slug}


def site(slug=SITE) -> dict:
    return {**_site_ref(slug), "custom_fields": {}}


def vlan(vid, name, *, group=SITE, tags=()) -> dict:
    return {
        "vid": vid,
        "name": name,
        "custom_fields": {"vnet": name},
        "status": {"value": "active"},
        "group": {"slug": group},
        "tags": [{"slug": t} for t in tags],
    }


def prefix(cidr, *, site_slug=SITE, vid=None, role=None, status="active") -> dict:
    return {
        "prefix": cidr,
        "status": {"value": status},
        "scope_type": "dcim.site" if site_slug else None,
        "scope": _site_ref(site_slug) if site_slug else None,
        "vlan": {"vid": vid} if vid is not None else None,
        "role": {"slug": role} if role else None,
    }


def address(addr, *, dns_name="", role=None, status="active", holder=None) -> dict:
    return {
        "address": addr,
        "dns_name": dns_name,
        "role": {"value": role} if role else None,
        "status": {"value": status},
        "assigned_object": {"name": "lo", "device": {"name": holder}} if holder else None,
    }


def asn(number, role, site_slug=SITE) -> dict:
    return {"asn": number, "role": {"slug": role}, "sites": [_site_ref(site_slug)]}


def community(value, purpose) -> dict:
    # The plugin's own `role` is an ID its UI never shows; the custom field is what's read.
    return {"value": value, "role": None, "custom_fields": {"role": {"slug": purpose}}}


def zone(name, view="_default_") -> dict:
    return {"name": name, "active": True, "default_ttl": 3600, "view": {"name": view},
            "nameservers": [{"name": "ratatoskr-dns.one.test"}]}


def dnssec_policy(name, *key_types, algorithm="ECDSAP256SHA256") -> dict:
    return {"name": name, "status": "active", "key_templates": [
        {"type": t, "algorithm": algorithm, "key_size": None} for t in key_types
    ]}


def record(zone_name, fqdn, rtype, value, ttl=None, view="_default_") -> dict:
    return {"zone": {"name": zone_name, "view": {"name": view}}, "fqdn": fqdn, "type": rtype,
            "value": value, "ttl": ttl, "active": True}


def soa(zone_name, view="_default_") -> dict:
    return record(zone_name, f"{zone_name}.", "SOA",
                  "ratatoskr-dns.one.test. hostmaster.one.test. 1 3600 600 2419200 300",
                  view=view)


def device(name, role, site_slug=SITE, tags=(), role_name=None) -> dict:
    return {
        "name": name,
        "role": {"slug": role, "name": role_name or role},
        "site": _site_ref(site_slug),
        "tags": [{"slug": t} for t in tags],
    }


@pytest.fixture
def snapshot() -> netbox.Snapshot:
    """One site: two VLANs, one of them dual-stack, a router holding their gateways, and a
    dns purpose carrying an anycast VIP and one resolver's service addresses."""
    return netbox.Snapshot(
        sites=[site()],
        vlans=[vlan(10, "management"), vlan(110, "servers", tags=["bgp-listen"])],
        prefixes=[
            prefix("10.0.0.0/16", status="container"),
            prefix("10.0.10.0/24", vid=10),
            prefix("10.0.110.0/24", vid=110),
            prefix("2001:db8:0:110::/64", vid=110),
            prefix("10.0.200.0/27", role="dns"),
            prefix("2001:db8::/64", role="dns"),
        ],
        addresses=[
            address("10.0.10.5/24", dns_name="hugin.one.test"),
            address("10.0.200.1/27", dns_name="dns.one.test", role="anycast"),
            address("2001:db8::53/64", dns_name="dns.one.test", role="anycast"),
            address("10.0.200.2/27", dns_name="ratatoskr-dns.one.test", role="loopback",
                    holder="ratatoskr"),
            address("2001:db8::2/64", dns_name="ratatoskr-dns.one.test", role="loopback",
                    holder="ratatoskr"),
            address("10.0.110.9/24", dns_name="ratatoskr.one.test", status="dhcp",
                    holder="ratatoskr"),
            address("2001:db8:0:110::9/64", dns_name="ratatoskr.one.test", status="slaac",
                    holder="ratatoskr"),
            address("192.0.2.9/24", dns_name="elsewhere.example"),
            address("10.0.10.254/24", holder="udm"),
            address("10.0.110.1/24", holder="udm"),
            address("2001:db8:0:110::1/64", holder="udm"),
        ],
        asns=[asn(64512, "router"), asn(64513, "dns")],
        communities=[community("64512:1", "dns")],
        zones=[zone("one.test"), zone("10.in-addr.arpa"), zone("8.b.d.0.1.0.0.2.ip6.arpa")],
        records=[
            soa("one.test"), soa("10.in-addr.arpa"), soa("8.b.d.0.1.0.0.2.ip6.arpa"),
            record("one.test", "dns.one.test.", "A", "10.0.200.1"),
        ],
        devices=[device("ratatoskr", "dns-resolver", tags=["dns-primary"]),
                 device("udm", "router-firewall", role_name="Router Firewall")],
    )

