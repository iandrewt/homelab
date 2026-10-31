"""NetBox, reshaped into what the generators and Ansible read."""

import ipaddress

from . import netbox

INTERNAL_VIEW = "_default_"
RESOLVER_ROLE = "dns-resolver"
RECURSOR_PURPOSE = "dns-recursor"
AUTHORITATIVE_PURPOSE = "dns-authoritative"
ROUTER_ROLE = "router"
# A segment's Role is its tier and kind
SEGMENT_ROLES = ("site-services", "site-instances", "home-services", "home-instances", "internet-services",
                 "internet-instances", "global-services")
GLOBAL_ROLE = "global"
# By name, which is all the netbox-site module can look a device role up by
ROUTER_DEVICE_ROLE = "Router Firewall"
# What a router's session may be, to be in its config: a planned one is there for its speaker
# to come up into
RENDERED_SESSIONS = ("active", "planned")
PRIMARY_TAG = "dns-primary"
# A zone whose owner writes its records over RFC 2136, which octoDNS must leave alone
RFC2136_TAG = "rfc2136"
# A Service's parent, in the terms `_host` keys a host by
HOST_TYPES = {"dcim.device": "device", "virtualization.virtualmachine": "vm"}
# A site's machines are named under <slug>.infra, beside its home network's <slug>.home
SUBSTRATE_LABEL = "infra"


def load(snapshot: netbox.Snapshot | None = None) -> dict:
    snap = netbox.fetch() if snapshot is None else snapshot
    zones = _zones(snap)
    return {
        "sites": {raw["slug"]: _site(raw, snap, zones) for raw in snap.sites},
        "zones": zones,
        # Where a name the public view writes lives: the nameservers', the recursor's
        "public_zones": sorted(z["name"] for z in snap.zones if z["view"]["name"] != INTERNAL_VIEW),
    }


def unzoned(model: dict) -> list[str]:
    """Names netbox_dns will skip without a word: no zone to hold the record, or its PTR."""

    def held(name: str) -> bool:
        return any(name == z or name.endswith(f".{z}") for z in [*model["zones"], *model["public_zones"]])

    problems = []
    for slug, site in model["sites"].items():
        for address, name in site["named"].items():
            if not held(name):
                problems.append(f"{slug}: {name} is in no zone")
            if not held(ipaddress.ip_address(address).reverse_pointer):
                problems.append(f"{slug}: {name} ({address}) has no reverse zone for its PTR")
    return problems


def _site(raw: dict, snap: netbox.Snapshot, zones: dict) -> dict:
    slug = raw["slug"]
    hosts = [
        h for h in [*snap.devices, *snap.virtual_machines] if _slug(h, "site") == slug
    ]
    routers = {_host(h) for h in hosts if (h.get("role") or {}).get("name") == ROUTER_DEVICE_ROLE}
    gateways = [_ip(a) for a in snap.addresses if _holder(a) in routers]
    vlans = _vlans(slug, snap, gateways)
    speakers = _speakers(slug, snap)

    # NetBox doesn't scope an IP to a site: an address is the site's when one of the site's
    # prefixes holds it, or when one of its speakers originates it
    nets = [_net(p) for p in _at(snap, slug)]
    owned = {a for o in speakers.values() for a in o["addresses"]}
    addresses = [a for a in snap.addresses if str(_ip(a)) in owned or any(_ip(a) in n for n in nets)]
    ranges = _ranges(slug, snap)
    return {
        "slug": slug,
        # A cloud region is a Site Group; a branch is in none
        "region": (raw.get("group") or {}).get("slug"),
        "forward_zone": next((z for z in sorted(zones) if z.split(".")[:2] == [slug, SUBSTRATE_LABEL]), None),
        "router_asn": next((n for n, o in speakers.items() if o["role"] == ROUTER_ROLE), None),
        # A console in Site Manager is found by its serial, its router's in NetBox
        "router_serial": next((h["serial"] for h in hosts if _host(h) in routers and h.get("serial")), None),
        "ranges": ranges,
        "gua_prefix": next((r for r in ranges if ":" in r), None),
        "internet_facing": sorted(
            (p["prefix"] for p in _at(snap, slug) if "internet" in (audience(p) or {}).get("zones", [])),
            key=_sort_net,
        ),
        "vlans": vlans,
        "peer_groups": _peer_groups(slug, snap),
        "policies": _policies(snap),
        "segments": _segments(slug, snap),
        "speakers": {n: o for n, o in speakers.items() if o["role"] != ROUTER_ROLE},
        "trusted": _trusted(slug, snap),
        "services": _services(hosts, addresses, snap),
        "dns": _dns(hosts, addresses, vlans, speakers, snap),
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
            "octodns": RFC2136_TAG not in _tags(raw),
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


def _vlans(slug: str, snap: netbox.Snapshot, gateways: list) -> dict:
    """A site's VLANs are in the group named by its slug. Each gateway is the router's
    address inside the VLAN's prefix - and a VLAN-only network has none."""
    nets: dict[int, dict] = {}
    for p in _at(snap, slug):
        if p.get("vlan") and _active(p):
            net = _net(p)
            held = nets.setdefault(p["vlan"]["vid"], {})
            other = held.get(net.version)
            # A sub-pool naming its VLAN too is inside the VLAN's network, which stays
            if other is None or net.supernet_of(other):
                held[net.version] = net
            elif not net.subnet_of(other):
                raise ValueError(f"VLAN {p['vlan']['vid']} at {slug} holds {other} and {net}; "
                                 "a VLAN is one network per family")

    vlans = {}
    for raw in snap.vlans:
        if not _active(raw) or _slug(raw, "group") != slug:
            continue
        v4, v6 = nets.get(raw["vid"], {}).get(4), nets.get(raw["vid"], {}).get(6)
        vlans[raw["name"]] = {
            "vid": raw["vid"],
            "vnet": (raw.get("custom_fields") or {}).get("vnet"),
            "cluster": _ref(raw.get("custom_fields"), "cluster"),
            "tags": _tags(raw),
            "ipv4": v4 and str(v4),
            "ipv6": v6 and str(v6),
            "gateway_ipv4": v4 and _gateway(v4, gateways),
            "gateway_ipv6": v6 and _gateway(v6, gateways),
        }
    return vlans


def _peer_groups(slug: str, snap: netbox.Snapshot) -> list[dict]:
    """The router's dynamic peering, by peer group: each session is one speaker group allowed
    to peer from one listen range, at the router's address there. A group's ranges are one
    family; an IPv4 one carries both families, an IPv6 one IPv6 alone."""
    groups = {g["name"]: g for g in snap.bgp_peer_groups}
    found: dict[str, dict] = {}
    for session in snap.bgp_sessions:
        if _slug(session, "site") != slug or _choice(session["status"]) not in RENDERED_SESSIONS:
            continue
        name = session["peer_group"]["name"]
        group = groups[name]
        attributes = group.get("extra_attributes") or {}
        entry = found.setdefault(name, {
            "name": name,
            "ranges": set(),
            "speakers": set(),
            "max_prefixes": 0,
            "timers": (attributes.get("keepalive"), attributes.get("holdtime")),
            "export": [p["name"] for p in group.get("export_policies") or []],
        })
        entry["ranges"].add(session["remote_prefix"]["prefix"])
        entry["speakers"].add(session["remote_as"]["asn"])
        entry["max_prefixes"] = max(entry["max_prefixes"], session.get("max_prefixes") or 0)
    peer_groups = []
    for entry in sorted(found.values(), key=lambda g: g["name"]):
        versions = {ipaddress.ip_network(r).version for r in entry["ranges"]}
        if len(versions) != 1:
            raise ValueError(f"peer group {entry['name']} listens on both families")
        peer_groups.append(entry | {
            "ranges": sorted(entry["ranges"], key=_sort_net),
            "speakers": sorted(entry["speakers"]),
            "families": ("ipv4", "ipv6") if versions == {4} else ("ipv6",),
        })
    return peer_groups


def _policies(snap: netbox.Snapshot) -> dict:
    """Each routing policy's rules, by index: an action, and nothing matched, which is all the
    router's own policies need."""
    policies: dict[str, list] = {}
    for rule in snap.routing_policy_rules:
        matches = [k for k in ("match_ip_address", "match_ipv6_address", "match_aspath_list", "match_community",
                               "match_community_list") if rule.get(k)]
        if matches or rule.get("match_custom") or rule.get("set_actions"):
            raise ValueError(f"routing policy {rule['routing_policy']['name']} rule {rule['index']} matches "
                             "or sets something, which the renderer doesn't")
        policies.setdefault(rule["routing_policy"]["name"], []).append((rule["index"], _choice(rule["action"])))
    return {name: sorted(rules) for name, rules in policies.items()}


def _speakers(slug: str, snap: netbox.Snapshot) -> dict:
    """Every speaker group at the site is its ASN, and what it may originate is what NetBox
    names it the origin of: prefixes naming it as `origin`, addresses naming it among
    `origins`."""
    speakers = {
        a["asn"]: {
            "name": a.get("description") or str(a["asn"]),
            "role": _slug(a, "role"),
            "cluster": _ref(a.get("custom_fields"), "cluster"),
            "prefixes": [],
            "addresses": [],
        }
        for a in snap.asns
        if slug in {s["slug"] for s in a.get("sites") or []}
    }
    for p in snap.prefixes:
        origin = _asn_ref((p.get("custom_fields") or {}).get("origin"))
        if origin in speakers:
            speakers[origin]["prefixes"].append(p["prefix"])
    for a in snap.addresses:
        for origin in map(_asn_ref, (a.get("custom_fields") or {}).get("origins") or []):
            if origin in speakers:
                speakers[origin]["addresses"].append(str(_ip(a)))
    for speaker in speakers.values():
        speaker["prefixes"].sort(key=_sort_net)
        speaker["addresses"].sort(key=lambda a: (ipaddress.ip_address(a).version, ipaddress.ip_address(a)))
    return speakers


def _segments(slug: str, snap: netbox.Snapshot) -> dict:
    """By Role: the site's own segments, and the global ones every site carries."""
    global_blocks = [_net(p) for p in snap.prefixes if _slug(p, "role") == GLOBAL_ROLE]
    segments: dict[str, dict] = {}
    for p in snap.prefixes:
        role = _slug(p, "role")
        if role not in SEGMENT_ROLES:
            continue
        net = _net(p)
        mine = _site_of(p, snap) == slug or any(net.subnet_of(g) for g in global_blocks
                                                     if g.version == net.version)
        if mine:
            segments.setdefault(role, {"ipv4": [], "ipv6": []})[f"ipv{net.version}"].append(str(net))
    return segments


def _trusted(slug: str | None, snap: netbox.Snapshot) -> dict:
    """Trust is a source: each tag names the sub-pools and VLANs' prefixes carrying it, and the
    addresses of the devices and VMs carrying it. A site of None is every site."""
    vids = {(_slug(v, "group"), v["vid"]): _tags(v) for v in snap.vlans}
    prefixes = snap.prefixes if slug is None else _at(snap, slug)
    trusted: dict[str, list[str]] = {}
    for p in prefixes:
        vlan = (_site_of(p, snap), (p.get("vlan") or {}).get("vid"))
        for tag in set(_tags(p)) | set(vids.get(vlan, [])):
            trusted.setdefault(tag, []).append(p["prefix"])
    # A host's tag covers its primary addresses, the ones it's reached from
    for h in [*snap.devices, *snap.virtual_machines]:
        if slug is not None and _slug(h, "site") != slug:
            continue
        for primary in (h.get("primary_ip4"), h.get("primary_ip6")):
            for tag in _tags(h) if primary else []:
                trusted.setdefault(tag, []).append(str(ipaddress.ip_interface(_ip(primary).exploded)))
    return {tag: sorted(set(nets), key=_sort_net) for tag, nets in sorted(trusted.items())}


def _zone(p: dict, vlan_zones: dict, snap: netbox.Snapshot) -> str | None:
    """A prefix's own zone, else its VLAN's Role."""
    vlan = (_site_of(p, snap), (p.get("vlan") or {}).get("vid"))
    return _slug(p.get("custom_fields"), "zone") or vlan_zones.get(vlan)


def sources(audience: dict, snap: netbox.Snapshot) -> dict:
    """An audience as the sources a firewall admits: each zone's prefixes at the audience's
    sites (all of them when it names none, and a global prefix at every site), and each trust
    tag's sources anywhere, each collapsed into what holds it. `internet` is no prefix of ours,
    so it stays a flag."""
    vlan_zones = {(_slug(v, "group"), v["vid"]): _slug(v, "role") for v in snap.vlans}
    sites = set(audience["sites"])
    nets = [p["prefix"] for p in snap.prefixes
            if _zone(p, vlan_zones, snap) in audience["zones"]
            and (not sites or _site_of(p, snap) in sites | {None})]
    trusted = _trusted(None, snap)
    nets += [n for tag in audience["tags"] for n in trusted.get(tag, [])]
    nets = [ipaddress.ip_network(n) for n in nets]

    def collapsed(version: int) -> list[str]:
        return [str(n) for n in ipaddress.collapse_addresses(n for n in nets if n.version == version)]

    return {"ipv4": collapsed(4), "ipv6": collapsed(6), "internet": "internet" in audience["zones"]}


def audience(raw: dict) -> dict | None:
    """A destination's audience custom fields, or None when it states none."""
    fields = raw.get("custom_fields") or {}
    zones = [z["slug"] for z in fields.get("audience") or []]
    tags = [t["slug"] for t in fields.get("audience_tags") or []]
    if not zones and not tags:
        return None
    return {"zones": zones, "tags": tags, "sites": [s["slug"] for s in fields.get("audience_sites") or []]}


def _services(hosts: list[dict], site_addresses: list[dict], snap: netbox.Snapshot) -> dict:
    """The Services of each host at the site, and of each FHRP group whose addresses are the
    site's, with the sources that may reach them: the Service's own audience, else that of the
    most specific prefix holding its address."""
    # A host's name is unique only within its site, and a nested FHRP group carries none
    names = {_host(h): h["name"] for h in hosts}
    mine = {a["address"] for a in site_addresses}
    groups = {g["id"]: g["name"] for g in snap.fhrp_groups}
    services: dict[str, dict] = {}
    for svc in snap.services:
        addresses = [a["address"] for a in svc.get("ipaddresses") or []]
        kind, parent = svc.get("parent_object_type"), svc.get("parent_object_id")
        if kind == "ipam.fhrpgroup":
            host = groups.get(parent) if any(a in mine for a in addresses) else None
        else:
            host = names.get((HOST_TYPES.get(kind), parent))
        if host is None:
            continue
        wanted = audience(svc)
        if wanted is None and addresses:
            ip = ipaddress.ip_interface(addresses[0]).ip
            holding = [p for p in snap.prefixes if ip in _net(p) and audience(p)]
            if holding:
                wanted = audience(max(holding, key=lambda p: _net(p).prefixlen))
        services.setdefault(host, {})[svc["name"]] = {
            "protocol": _choice(svc["protocol"]),
            "ports": svc["ports"],
            "addresses": [str(ipaddress.ip_interface(a).ip) for a in addresses],
            "sources": sources(wanted, snap) if wanted else None,
        }
    return services


def _dns(hosts: list[dict], addresses: list[dict], vlans: dict, speakers: dict,
         snap: netbox.Snapshot) -> dict:
    """The recursor, which is global, so every site's - a spoke with no fleet of its own
    included; and each server's own authoritative address, never the recursor's, with the
    VLAN it's on and the speaker group originating it."""
    pools = {
        purpose: [_net(p) for p in snap.prefixes if _slug(p, "role") == purpose]
        for purpose in (RECURSOR_PURPOSE, AUTHORITATIVE_PURPOSE)
    }

    def inside(a: dict | str, purpose: str) -> bool:
        ip = ipaddress.ip_address(a) if isinstance(a, str) else _ip(a)
        return any(ip in n for n in pools[purpose] if n.version == ip.version)

    recursor = {_ip(a).version: str(_ip(a)) for a in snap.addresses if inside(a, RECURSOR_PURPOSE)}

    servers = {}
    for host in hosts:
        if _slug(host, "role") != RESOLVER_ROLE:
            continue
        held = [a for a in addresses if _holder(a) == _host(host)]
        authoritative = {_ip(a).version: str(_ip(a)) for a in held if inside(a, AUTHORITATIVE_PURPOSE)}
        # None until it's leased an address, as a resolver mid-rebuild hasn't
        vlan = next(
            (name
             for a in held
             for name, v in vlans.items()
             for net in (v["ipv4"], v["ipv6"])
             if net and _ip(a) in ipaddress.ip_network(net)),
            None,
        )
        asn = next((n for n, o in speakers.items() if set(authoritative.values()) & set(o["addresses"])), None)
        servers[host["name"]] = {
            "ipv4": authoritative.get(4), "ipv6": authoritative.get(6), "vlan": vlan, "asn": asn,
            "primary": PRIMARY_TAG in _tags(host),
        }
    return {
        "recursor": {"ipv4": recursor.get(4), "ipv6": recursor.get(6)} if recursor else None,
        "servers": servers,
    }


def _ranges(slug: str, snap: netbox.Snapshot) -> list[str]:
    """The widest container the site holds in each family: all of its own addresses, and in
    IPv6 what an ISP delegated it."""
    containers = [_net(p) for p in _at(snap, slug) if p["status"]["value"] == "container"]
    return [str(min(nets, key=lambda n: n.prefixlen))
            for version in (4, 6) if (nets := [n for n in containers if n.version == version])]


def _at(snap: netbox.Snapshot, slug: str) -> list[dict]:
    return [p for p in snap.prefixes if _site_of(p, snap) == slug]


def _site_of(p: dict, snap: netbox.Snapshot) -> str | None:
    """The site a prefix is scoped to, or to a Location at, as NetBox's own `site_id` filter has
    it: an AZ's networks are its site's. The nested Location names no site, so the join is by id
    within this one fetch."""
    if p.get("scope_type") == "dcim.site":
        return _slug(p, "scope")
    if p.get("scope_type") == "dcim.location":
        return next((_slug(loc, "site") for loc in snap.locations if loc["id"] == p["scope_id"]), None)
    return None


def _gateway(net, gateways: list) -> str | None:
    return next((str(ip) for ip in gateways if ip in net), None)


def _tags(raw: dict) -> list[str]:
    return sorted(t["slug"] for t in raw.get("tags") or [])


def _slug(raw: dict, key: str) -> str | None:
    return ((raw or {}).get(key) or {}).get("slug")


def _ref(raw: dict | None, key: str) -> str | None:
    return ((raw or {}).get(key) or {}).get("name")


def _asn_ref(raw: dict | None) -> int | None:
    return (raw or {}).get("asn")


def _choice(raw) -> str | None:
    """A choice custom field comes back as its value, or as {value, label}."""
    return raw.get("value") if isinstance(raw, dict) else raw


def _active(raw: dict) -> bool:
    return raw["status"]["value"] == "active"


def _net(raw: dict):
    return ipaddress.ip_network(raw["prefix"])


def _sort_net(cidr: str):
    net = ipaddress.ip_network(cidr)
    return net.version, net


def _ip(raw: dict):
    return ipaddress.ip_interface(raw["address"]).ip


def _holder(raw: dict) -> tuple[str, int] | None:
    """The host an address's interface is on. A device's name is unique only within its site,
    and the nested host carries no site, so the join is by its id within this one fetch."""
    target = raw.get("assigned_object") or {}
    if target.get("device"):
        return "device", target["device"]["id"]
    if target.get("virtual_machine"):
        return "vm", target["virtual_machine"]["id"]
    return None


def _host(raw: dict) -> tuple[str, int]:
    return ("device" if "device_type" in raw else "vm"), raw["id"]
