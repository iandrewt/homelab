import pytest
from homelab import netbox

SITE = "one"


def _site_ref(slug: str) -> dict:
    return {"name": f"{slug.title()}ville", "slug": slug}


def site(slug=SITE) -> dict:
    return {**_site_ref(slug), "custom_fields": {}}


def vlan_group(slug=SITE) -> dict:
    return {"slug": slug, "scope_type": "dcim.site", "scope": _site_ref(slug)}


def vlan(vid, name, *, group=SITE, tags=()) -> dict:
    return {
        "vid": vid,
        "name": name,
        "status": {"value": "active"},
        "group": {"slug": group},
        "site": None,
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


def ip_range(start, end, status="active") -> dict:
    return {"start_address": start, "end_address": end, "status": {"value": status}}


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


def zone(name) -> dict:
    return {"name": name, "active": True, "default_ttl": 3600,
            "nameservers": [{"name": "ratatoskr-dns.one.test"}]}


def record(zone_name, fqdn, rtype, value, ttl=None) -> dict:
    return {"zone": {"name": zone_name}, "fqdn": fqdn, "type": rtype, "value": value,
            "ttl": ttl, "active": True}


def device(name, role, site_slug=SITE) -> dict:
    return {
        "name": name,
        "role": {"slug": role},
        "platform": {"slug": "debian-13"},
        "site": _site_ref(site_slug),
    }


@pytest.fixture
def snapshot() -> netbox.Snapshot:
    """One site: two VLANs, one of them dual-stack with a pool, and a dns purpose
    carrying an anycast VIP and one resolver's service addresses."""
    return netbox.Snapshot(
        sites=[site()],
        vlan_groups=[vlan_group()],
        vlans=[vlan(10, "management"), vlan(110, "servers", tags=["bgp-listen"])],
        prefixes=[
            prefix("10.0.0.0/16", status="container"),
            prefix("10.0.10.0/24", vid=10),
            prefix("10.0.110.0/24", vid=110),
            prefix("2001:db8:0:110::/64", vid=110),
            prefix("10.0.200.0/27", role="dns"),
            prefix("2001:db8::/64", role="dns"),
        ],
        ranges=[
            ip_range("10.0.10.16/24", "10.0.10.254/24"),
            ip_range("10.0.110.2/24", "10.0.110.254/24"),
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
            address("192.0.2.9/24", dns_name="elsewhere.example"),
        ],
        asns=[asn(64512, "router"), asn(64513, "dns")],
        communities=[community("64512:1", "dns")],
        zones=[zone("one.test"), zone("10.in-addr.arpa"), zone("8.b.d.0.1.0.0.2.ip6.arpa")],
        records=[record("one.test", "dns.one.test.", "A", "10.0.200.1")],
        devices=[device("ratatoskr", "dns-resolver")],
    )


@pytest.fixture
def ipam(snapshot):
    from homelab.model import load

    return load(snapshot)


@pytest.fixture
def repo(tmp_path):
    from homelab.repo import MARKER, Repo

    (tmp_path / MARKER).write_text("[project]\n")
    for sub in ("tofu/clusters", "ansible/inventory/group_vars", "unifi/generated"):
        (tmp_path / sub).mkdir(parents=True)
    return Repo(tmp_path)


@pytest.fixture
def cluster() -> dict:
    """A cluster file that passes every check, with one of each section to break."""
    return {
        "name": "one",
        "site": SITE,
        "endpoint": "https://hugin.one.test:8006/",
        "nodes": {
            "hugin": {
                "pools": {"pool": {}},
                "management": {"uplink": "eno2"},
            }
        },
        "storages": {
            "pool": {"type": "zfspool", "pool": "pool", "nodes": ["hugin"]},
            "images": {"type": "zfspool", "pool": "pool", "nodes": ["hugin"]},
            "local": {"type": "directory", "nodes": ["hugin"]},
        },
        "guest_defaults": {"disk_storage": "pool", "snippet_storage": "local"},
        "workloads": {},
        "firewall": {"management_vlans": ["management"]},
    }
