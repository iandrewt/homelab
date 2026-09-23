"""NetBox, reshaped into what the generators and Ansible read."""

import ipaddress

from . import netbox

INTERNAL_VIEW = "_default_"
RESOLVER_ROLE = "dns-resolver"
RESOLVER_PURPOSE = "dns"
ROUTER_ROLE = "router"
LISTEN_TAG = "bgp-listen"


def load(snapshot: netbox.Snapshot | None = None) -> dict:
    snap = netbox.fetch() if snapshot is None else snapshot
    zones = _zones(snap)
    return {
        "sites": {raw["slug"]: _site(raw["slug"], snap, zones) for raw in snap.sites},
        "zones": zones,
    }


def unzoned(ipam: dict) -> list[str]:
    """Names netbox_dns will skip without a word: no zone to hold the record, or its PTR."""

    def held(name: str) -> bool:
        return any(name == z or name.endswith(f".{z}") for z in ipam["zones"])

    problems = []
    for slug, site in ipam["sites"].items():
        for address, name in site["named"].items():
            if not held(name):
                problems.append(f"{slug}: {name} is in no zone")
            if not held(ipaddress.ip_address(address).reverse_pointer):
                problems.append(f"{slug}: {name} ({address}) has no reverse zone for its PTR")
    return problems


def _site(slug: str, snap: netbox.Snapshot, zones: dict) -> dict:
    vlans, listen = _vlans(slug, snap)
    pools = _pools(slug, snap)
    owned = [
        ipaddress.ip_network(net)
        for block in [*vlans.values(), *pools.values()]
        for net in (block["ipv4"], block["ipv6"])
        if net
    ]
    addresses = [a for a in snap.addresses if any(_ip(a) in net for net in owned)]
    hosts = [
        h for h in [*snap.devices, *snap.virtual_machines] if _slug(h, "site") == slug
    ]
    return {
        "slug": slug,
        "forward_zone": next((z for z in sorted(zones) if z.split(".")[0] == slug), None),
        "router_asn": _asn(snap, slug, ROUTER_ROLE),
        "gua_prefix": _gua(slug, snap),
        "vlans": vlans,
        "listen_vlans": listen,
        "pools": pools,
        "anycast": _anycast(addresses, pools),
        "resolvers": _resolvers(hosts, addresses, vlans, pools),
        "hosts": sorted(h["name"] for h in hosts),
        "named": {str(_ip(a)): a["dns_name"] for a in addresses if a.get("dns_name")},
    }


def _zones(snap: netbox.Snapshot) -> dict:
    """Who serves each internal zone, the SOA it's created with, and its keys. octoDNS
    writes the records."""

    def internal(zone: dict) -> bool:
        return zone["view"]["name"] == INTERNAL_VIEW

    soas = {r["zone"]["name"]: r for r in snap.records if r["type"] == "SOA" and internal(r["zone"])}
    policies = {p["name"]: p for p in snap.dnssec_policies}
    zones = {}
    for raw in snap.zones:
        if not raw["active"] or not internal(raw):
            continue
        name = raw["name"]
        policy = policies.get((raw.get("dnssec_policy") or {}).get("name"))
        signed = policy and policy["status"] == "active"
        zones[name] = {
            "name": name,
            "nameservers": sorted(ns["name"] for ns in raw["nameservers"]),
            "soa": soas[name]["value"],
            "soa_ttl": soas[name]["ttl"] or raw["default_ttl"],
            # In the lowercase key types PowerDNS's API takes
            "dnssec_keys": [
                {"keytype": t["type"].lower(), "algorithm": t["algorithm"],
                 **({"bits": t["key_size"]} if t["key_size"] else {})}
                for t in sorted(policy["key_templates"], key=lambda t: t["type"])
            ] if signed else [],
        }
    return zones


def _vlans(slug: str, snap: netbox.Snapshot) -> tuple[dict, list[str]]:
    """A VLAN in a group has no site of its own - the group's scope places it."""
    groups = {g["slug"] for g in _at(snap.vlan_groups, slug)}
    nets: dict[int, dict] = {}
    for p in _at(snap.prefixes, slug):
        if p.get("vlan") and _active(p):
            net = ipaddress.ip_network(p["prefix"])
            nets.setdefault(p["vlan"]["vid"], {})[net.version] = net

    vlans, listen = {}, []
    for raw in snap.vlans:
        if not _active(raw) or not (_slug(raw, "group") in groups or _slug(raw, "site") == slug):
            continue
        v4, v6 = nets[raw["vid"]][4], nets[raw["vid"]].get(6)
        vlans[raw["name"]] = {
            "vid": raw["vid"],
            "ipv4": str(v4),
            "ipv6": v6 and str(v6),
            "gateway_ipv4": str(v4.network_address + 1),
            "gateway_ipv6": v6 and str(v6.network_address + 1),
        }
        if LISTEN_TAG in {t["slug"] for t in raw["tags"]}:
            listen.append(raw["name"])
    return vlans, sorted(listen)


def _pools(slug: str, snap: netbox.Snapshot) -> dict:
    """One per purpose: a purpose is whatever a community is tagged for, so adding one is
    a community and a prefix with that role."""
    communities = {
        role["slug"]: c["value"]
        for c in snap.communities
        if (role := (c.get("custom_fields") or {}).get("role"))
    }
    pools: dict[str, dict] = {}
    for p in _at(snap.prefixes, slug):
        purpose = _slug(p, "role")
        if purpose in communities:
            pool = pools.setdefault(purpose, {
                "ipv4": None, "ipv6": None, "community": communities[purpose],
                "speaker_asn": _asn(snap, slug, purpose),
            })
            net = ipaddress.ip_network(p["prefix"])
            pool[f"ipv{net.version}"] = str(net)
    return pools


def _anycast(addresses: list[dict], pools: dict) -> dict | None:
    vips = {_ip(a).version: str(_ip(a)) for a in _in_pool(addresses, pools, "anycast")}
    return {"ipv4": vips[4], "ipv6": vips[6]} if vips else None


def _resolvers(hosts: list[dict], addresses: list[dict], vlans: dict, pools: dict) -> dict:
    """Each resolver's service addresses - the loopbacks it holds in the resolvers' block -
    and the VLAN its LAN address is in, which is where it peers from."""
    loopbacks = _in_pool(addresses, pools, "loopback")
    resolvers = {}
    for host in hosts:
        if _slug(host, "role") != RESOLVER_ROLE:
            continue
        service = {_ip(a).version: str(_ip(a)) for a in loopbacks if _holder(a) == host["name"]}
        lan = next(
            name
            for a in addresses
            if _holder(a) == host["name"]
            for name, vlan in vlans.items()
            for net in (vlan["ipv4"], vlan["ipv6"])
            if net and _ip(a) in ipaddress.ip_network(net)
        )
        resolvers[host["name"]] = {"ipv4": service[4], "ipv6": service[6], "vlan": lan}
    return resolvers


def _in_pool(addresses: list[dict], pools: dict, role: str) -> list[dict]:
    pool = pools.get(RESOLVER_PURPOSE) or {}
    nets = [ipaddress.ip_network(n) for n in (pool.get("ipv4"), pool.get("ipv6")) if n]
    return [
        a for a in addresses
        if (a.get("role") or {}).get("value") == role and any(_ip(a) in n for n in nets)
    ]


def _asn(snap: netbox.Snapshot, slug: str, role: str) -> int | None:
    return next(
        (a["asn"] for a in snap.asns
         if _slug(a, "role") == role and slug in {s["slug"] for s in a["sites"]}),
        None,
    )


def _gua(slug: str, snap: netbox.Snapshot) -> str | None:
    """The widest IPv6 container the site holds - what an ISP delegated it."""
    containers = [
        ipaddress.ip_network(p["prefix"])
        for p in _at(snap.prefixes, slug)
        if p["status"]["value"] == "container"
    ]
    v6 = [n for n in containers if n.version == 6]
    return str(min(v6, key=lambda n: n.prefixlen)) if v6 else None


def _at(objects: list[dict], slug: str) -> list[dict]:
    return [o for o in objects if o.get("scope_type") == "dcim.site" and _slug(o, "scope") == slug]


def _slug(raw: dict, key: str) -> str | None:
    return (raw.get(key) or {}).get("slug")


def _active(raw: dict) -> bool:
    return raw["status"]["value"] == "active"


def _ip(raw: dict):
    return ipaddress.ip_interface(raw["address"]).ip


def _holder(raw: dict) -> str | None:
    target = raw.get("assigned_object") or {}
    return (target.get("device") or target.get("virtual_machine") or {}).get("name")
