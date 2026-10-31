import zlib

import pytest
from homelab import netbox

SITE = "one"
OTHER = "two"
SPOKE = "three"

ROUTER_ONE, DNS_ONE, K8S_1 = 4210000000, 4210001000, 4210002000
ROUTER_TWO, DNS_TWO = 4210100000, 4210101000


def _site_ref(slug: str) -> dict:
    return {"name": f"{slug.title()}ville", "slug": slug}


def site(slug=SITE) -> dict:
    return {**_site_ref(slug), "custom_fields": {}}


def vlan(vid, name, *, group=SITE, tags=(), vnet=None, zone=None) -> dict:
    return {
        "vid": vid,
        "name": name,
        "custom_fields": {"vnet": vnet},
        "role": zone and {"slug": zone},
        "status": {"value": "active"},
        "group": {"slug": group},
        "tags": [{"slug": t} for t in tags],
    }


def location(slug, site_slug=SITE) -> dict:
    return {"id": _id(slug), "name": slug, "slug": slug, "site": _site_ref(site_slug)}


def prefix(cidr, *, site_slug=SITE, vid=None, role=None, status="active", origin=None,
           tags=(), zone=None, at=None) -> dict:
    """Scoped to the site, or to the Location `at`."""
    fields = {"origin": {"asn": origin} if origin else None, "zone": zone and {"slug": zone}}
    scope = ({"scope_type": "dcim.location", "scope_id": at["id"], "scope": {"id": at["id"], "slug": at["slug"]}}
             if at else
             {"scope_type": "dcim.site" if site_slug else None, "scope": _site_ref(site_slug) if site_slug else None})
    return {
        "prefix": cidr,
        "status": {"value": status},
        **scope,
        "vlan": {"vid": vid} if vid is not None else None,
        "role": {"slug": role} if role else None,
        "tags": [{"slug": t} for t in tags],
        "custom_fields": fields,
    }


def segment(cidr, role, *, site_slug=SITE) -> dict:
    return prefix(cidr, site_slug=site_slug, role=role, status="container", zone="workloads")


def address(addr, *, dns_name="", role=None, status="active", holder=None, origins=()) -> dict:
    return {
        "address": addr,
        "dns_name": dns_name,
        "role": {"value": role} if role else None,
        "status": {"value": status},
        "assigned_object": {"name": "lo", "device": {"id": _id(holder), "name": holder}} if holder else None,
        "custom_fields": {"origins": [{"asn": o} for o in origins]},
    }


def asn(number, role, site_slug=SITE, *, name="", cluster=None) -> dict:
    return {"asn": number, "role": {"slug": role}, "sites": [_site_ref(site_slug)],
            "description": name, "custom_fields": {"cluster": cluster and {"name": cluster}}}


def peer_group(name, tcp_md5=False) -> dict:
    return {"name": name, "extra_attributes": {"keepalive": 3, "holdtime": 9, "tcp_md5": tcp_md5},
            "export_policies": [{"name": "NOTHING"}]}


def session(asn, group, remote_prefix, *, site_slug=SITE, router="udm", status="active", max_prefixes=1000) -> dict:
    return {"site": _site_ref(site_slug), "device": {"name": router}, "status": {"value": status},
            "peer_group": {"name": group}, "remote_prefix": {"prefix": remote_prefix}, "remote_as": {"asn": asn},
            "max_prefixes": max_prefixes}


def zone(name, view="_default_", tags=()) -> dict:
    return {"name": name, "active": True, "default_ttl": 3600, "view": {"name": view},
            "nameservers": [{"name": "ratatoskr-dns.one.test"}], "tags": [{"slug": t} for t in tags]}


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


def _id(name: str) -> int:
    return zlib.crc32(name.encode())


def device(name, role, site_slug=SITE, tags=(), role_name=None, primary_ip6=None, id=None) -> dict:
    return {
        "id": id or _id(name),
        "device_type": {"model": "test"},
        "name": name,
        "primary_ip6": primary_ip6 and {"address": primary_ip6},
        "role": {"slug": role, "name": role_name or role},
        "site": _site_ref(site_slug),
        "tags": [{"slug": t} for t in tags],
    }


def service(name, host, ports, *, addresses=()) -> dict:
    return {
        "name": name,
        "protocol": {"value": "tcp"},
        "ports": list(ports),
        "parent_object_type": "dcim.device",
        "parent_object_id": host["id"],
        "parent": {"id": host["id"], "name": host["name"]},
        "ipaddresses": [{"address": a} for a in addresses],
        "custom_fields": {},
    }


@pytest.fixture
def snapshot() -> netbox.Snapshot:
    """Two sites, each with its own DNS fleet, and a spoke with none; the recursor is one global address both
    fleets originate. `one` also runs a Kubernetes cluster, with a pod block, a trusted
    sub-pool, and an API address in a pool every cluster's API shares; it also has an
    IPv6-only, VLAN-only network."""
    return netbox.Snapshot(
        sites=[site(), site(OTHER), site(SPOKE)],
        vlans=[
            vlan(10, "network"),
            vlan(210, "san", vnet="san"),
            vlan(300, "servers", tags=["dns-api"], vnet="servers"),
            vlan(81, "k8s-1", vnet="k8s-1"),
            vlan(300, "servers", group=OTHER),
        ],
        prefixes=[
            # global, carried by every site
            prefix("10.239.0.0/16", site_slug=None, role="global", status="container"),
            segment("10.239.0.0/21", "global-services", site_slug=None),
            prefix("10.239.0.0/26", site_slug=None, role="dns-recursor"),
            prefix("2001:db8::/52", site_slug=None, role="global", status="container"),
            segment("2001:db8::/56", "global-services", site_slug=None),
            prefix("2001:db8::/64", site_slug=None, role="dns-recursor"),
            # one
            prefix("10.0.0.0/16", role="hub", status="container"),
            prefix("10.0.0.64/26", vid=10),
            prefix("10.0.8.0/24", role="kubernetes-nodes", status="container"),
            prefix("2001:db8:1:1080::/60", role="kubernetes-nodes", status="container"),
            prefix("10.0.8.0/27", vid=81),
            prefix("10.0.30.0/24", vid=300),
            prefix("2001:db8:1:1300::/64", vid=300),
            prefix("2001:db8:1:1210::/64", vid=210),
            segment("10.0.16.0/20", "site-services"),
            segment("10.0.32.0/21", "home-services"),
            segment("10.0.40.0/21", "home-instances"),
            segment("10.0.128.0/17", "site-instances"),
            prefix("10.0.16.128/27", role="kubernetes-api"),
            prefix("10.0.40.0/28", role="dns-authoritative"),
            prefix("2001:db8:1:3100::/64", role="dns-authoritative"),
            prefix("10.0.128.0/21", role="pods", origin=K8S_1),
            prefix("10.0.132.0/27", role="pods-storage", origin=K8S_1, tags=["proxmox-api"]),
            # two
            prefix("10.1.0.0/16", site_slug=OTHER, role="hub", status="container"),
            prefix("10.1.30.0/24", site_slug=OTHER, vid=300),
            segment("10.1.40.0/21", "home-instances", site_slug=OTHER),
            prefix("10.1.40.0/28", site_slug=OTHER, role="dns-authoritative"),
        ],
        addresses=[
            address("10.239.0.53/26", dns_name="dns.one.test", role="anycast",
                    origins=[DNS_ONE, DNS_TWO]),
            address("2001:db8::53/64", dns_name="dns.one.test", role="anycast",
                    origins=[DNS_ONE, DNS_TWO]),
            address("10.0.16.128/27", dns_name="k8s-1.one.test", role="anycast", origins=[K8S_1]),
            address("10.0.40.1/28", dns_name="ratatoskr-dns.one.test", role="loopback",
                    holder="ratatoskr", origins=[DNS_ONE]),
            address("2001:db8:1:3100::1/64", dns_name="ratatoskr-dns.one.test", role="loopback",
                    holder="ratatoskr", origins=[DNS_ONE]),
            address("10.0.30.9/24", dns_name="ratatoskr.one.test", status="dhcp",
                    holder="ratatoskr"),
            address("2001:db8:1:1300::9/64", dns_name="ratatoskr.one.test", status="slaac",
                    holder="ratatoskr"),
            address("10.1.40.1/28", dns_name="munin-dns.two.test", role="loopback",
                    holder="munin", origins=[DNS_TWO]),
            address("10.1.30.9/24", holder="munin", status="dhcp"),
            address("192.0.2.9/24", dns_name="elsewhere.example"),
            address("10.0.0.65/26", holder="udm"),
            address("10.0.8.1/27", holder="udm"),
            address("10.0.30.1/24", holder="udm"),
            address("2001:db8:1:1300::1/64", holder="udm"),
            address("10.1.30.1/24", holder="udm-two"),
        ],
        asns=[
            asn(ROUTER_ONE, "router"), asn(DNS_ONE, "dns"),
            asn(K8S_1, "kubernetes", name="k8s-1", cluster="k8s-1"),
            asn(ROUTER_TWO, "router", OTHER), asn(DNS_TWO, "dns", OTHER),
        ],
        bgp_peer_groups=[peer_group("SERVICES"), peer_group("KUBERNETES-NODES", tcp_md5=True), peer_group("KUBERNETES-NODES-V6")],
        bgp_sessions=[
            session(DNS_ONE, "SERVICES", "10.0.30.0/24"),
            session(K8S_1, "KUBERNETES-NODES", "10.0.8.0/24"),
            session(K8S_1, "KUBERNETES-NODES-V6", "2001:db8:1:1080::/60"),
            # A limit is optional
            session(DNS_TWO, "SERVICES", "10.1.30.0/24", site_slug=OTHER, router="udm-two", max_prefixes=None),
        ],
        routing_policy_rules=[{"routing_policy": {"name": "NOTHING"}, "index": 10, "action": "deny"}],
        zones=[zone("one.test"), zone("10.in-addr.arpa"), zone("8.b.d.0.1.0.0.2.ip6.arpa")],
        records=[
            soa("one.test"), soa("10.in-addr.arpa"), soa("8.b.d.0.1.0.0.2.ip6.arpa"),
            record("one.test", "dns.one.test.", "A", "10.239.0.53"),
        ],
        devices=[
            device("ratatoskr", "dns-resolver", tags=["dns-primary"]),
            device("udm", "router-firewall", role_name="Router Firewall"),
            device("munin", "dns-resolver", OTHER),
            device("udm-two", "router-firewall", OTHER, role_name="Router Firewall"),
        ],
    )
