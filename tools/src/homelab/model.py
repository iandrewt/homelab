"""NetBox, parsed and checked once.

A generator that needs a `.get()` or a `try` is a missing check here.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from . import netbox

ROUTER_ROLE = "router"
RESOLVER_ROLE = "dns-resolver"
RESOLVER_PURPOSE = "dns"
LISTEN_TAG = "bgp-listen"
INTERNAL_VIEW = "_default_"

IPv4Net = ipaddress.IPv4Network
IPv6Net = ipaddress.IPv6Network


class InvalidSource(Exception):
    """NetBox holds something the generators could not render from."""


@dataclass(frozen=True)
class Vlan:
    name: str
    vid: int
    ipv4: IPv4Net
    ipv6: IPv6Net | None
    pool: tuple[str, str] | None

    @property
    def gateway_ipv4(self) -> ipaddress.IPv4Address:
        return self.ipv4.network_address + 1

    @property
    def gateway_ipv6(self) -> ipaddress.IPv6Address | None:
        return self.ipv6.network_address + 1 if self.ipv6 else None


@dataclass(frozen=True)
class BgpPool:
    purpose: str
    ipv4: IPv4Net
    ipv6: IPv6Net | None
    speaker_asn: int | None
    community: str


@dataclass(frozen=True)
class Address:
    address: ipaddress.IPv4Address | ipaddress.IPv6Address
    prefixlen: int
    dns_name: str
    role: str
    status: str
    holder: str

    @property
    def family(self) -> str:
        return "ipv4" if self.address.version == 4 else "ipv6"


@dataclass(frozen=True)
class Host:
    name: str
    role: str
    platform: str


@dataclass(frozen=True)
class Resolver:
    name: str
    fqdn: str
    ipv4: ipaddress.IPv4Address
    ipv6: ipaddress.IPv6Address
    vlan: str


@dataclass(frozen=True)
class Anycast:
    fqdn: str
    ipv4: ipaddress.IPv4Address
    ipv6: ipaddress.IPv6Address


@dataclass(frozen=True)
class Zone:
    name: str
    nameservers: tuple[str, ...]
    soa: str
    soa_ttl: int


@dataclass(frozen=True)
class Site:
    slug: str
    forward_zone: str | None
    router_asn: int | None
    listen_vlans: tuple[str, ...]
    gua_prefix: IPv6Net | None
    vlans: dict[str, Vlan]
    pools: dict[str, BgpPool]
    addresses: tuple[Address, ...]
    hosts: dict[str, Host]
    resolvers: dict[str, Resolver]
    anycast: Anycast | None

    def vlan(self, name: str) -> Vlan:
        return self.vlans[name]

    @property
    def router_id(self):
        """An identifier, not an address to reach - but one the router demonstrably holds."""
        return min(self.vlans.values(), key=lambda v: v.vid).gateway_ipv4

    def named(self) -> list[Address]:
        return sorted((a for a in self.addresses if a.dns_name), key=lambda a: a.dns_name)

    def in_pool(self, purpose: str, role: str) -> list[Address]:
        return _in_pool(self.addresses, self.pools.get(purpose), role)


@dataclass(frozen=True)
class Ipam:
    sites: dict[str, Site]
    zones: dict[str, Zone]

    def site(self, slug: str) -> Site:
        return self.sites[slug]


def load(snapshot: netbox.Snapshot | None = None) -> Ipam:
    return _build(snapshot if snapshot is not None else netbox.fetch())


def _build(snap: netbox.Snapshot) -> Ipam:
    errors: list[str] = []

    def fail(message: str) -> None:
        errors.append(message)

    communities = _communities(snap, fail)
    zones = _zones(snap, fail)
    sites = {
        raw["slug"]: _site(raw["slug"], snap, communities, zones, fail) for raw in snap.sites
    }

    if not sites:
        fail("no sites in NetBox")
    if errors:
        raise InvalidSource("\n".join(f"  - {e}" for e in sorted(set(errors))))
    return Ipam(sites=sites, zones=zones)


def _cf(raw: dict, key: str):
    return (raw.get("custom_fields") or {}).get(key)


def _slug(raw: dict, key: str) -> str:
    return (raw.get(key) or {}).get("slug") or ""


def _scoped(objects, slug: str) -> list[dict]:
    return [o for o in objects if o.get("scope_type") == "dcim.site" and _slug(o, "scope") == slug]


def _net(value: str):
    try:
        return ipaddress.ip_network(value, strict=False)
    except ValueError:
        return None


def _communities(snap: netbox.Snapshot, fail) -> dict[str, str]:
    """Purpose to community. A purpose is whatever a community is tagged for, so adding
    one is a community and a prefix with its role - nothing here changes."""
    tagged: dict[str, str] = {}
    for raw in snap.communities:
        purpose = (_cf(raw, "role") or {}).get("slug")
        if not purpose:
            continue
        if purpose in tagged:
            fail(f"communities {tagged[purpose]} and {raw['value']} both tag {purpose}")
            continue
        tagged[purpose] = raw["value"]
    return tagged


def _site(slug: str, snap: netbox.Snapshot, communities, zones, fail) -> Site:
    vlans, listen = _vlans(slug, snap, fail)
    pools = _pools(slug, snap, communities, fail)
    addresses = _addresses(snap, vlans, pools)
    hosts = _hosts(slug, snap)
    router_asn = _asn(slug, ROUTER_ROLE, snap, fail)
    anycast = _anycast(slug, addresses, pools, fail)
    resolvers = _resolvers(slug, hosts, vlans, listen, pools, addresses, fail)

    zone = _forward_zone(slug, zones, fail)
    if resolvers and not (anycast and router_asn and pools[RESOLVER_PURPOSE].speaker_asn):
        fail(
            f"{slug}: resolvers need an anycast address per family, an ASN with role "
            f"{ROUTER_ROLE} and one with role {RESOLVER_PURPOSE}"
        )
    _zone_coverage(slug, addresses, zones, fail)

    return Site(
        slug=slug,
        forward_zone=zone,
        router_asn=router_asn,
        listen_vlans=listen,
        gua_prefix=_gua(slug, snap),
        vlans=vlans,
        pools=pools,
        addresses=addresses,
        hosts=hosts,
        resolvers=resolvers,
        anycast=anycast,
    )


def _zones(snap: netbox.Snapshot, fail) -> dict[str, Zone]:
    """Every active zone in the internal view. octoDNS writes their records; what's left
    for the resolvers to know is who serves each zone and the SOA it's created with."""
    zones = {}
    for raw in snap.zones:
        if not raw["active"] or raw["view"]["name"] != INTERNAL_VIEW:
            continue
        name = raw["name"]
        nameservers = tuple(sorted(ns["name"] for ns in raw.get("nameservers") or []))
        if not nameservers:
            fail(f"zone {name}: no name servers")
            continue
        soas = [
            (r["value"], r["ttl"] or raw["default_ttl"])
            for r in snap.records
            if r["active"] and r["type"] == "SOA"
            and r["zone"]["name"] == name and r["zone"]["view"]["name"] == INTERNAL_VIEW
        ]
        if len(soas) != 1:
            fail(f"zone {name}: {len(soas)} SOA records, not 1")
            continue
        (soa, soa_ttl), = soas
        zones[name] = Zone(name=name, nameservers=nameservers, soa=soa, soa_ttl=soa_ttl)
    return zones


def _forward_zone(slug: str, zones: dict[str, Zone], fail) -> str | None:
    """The zone whose first label is the site's slug - the same convention that finds a
    site's VLAN group, and one the zone's own name states."""
    names = sorted(name for name in zones if name.split(".")[0] == slug)
    if len(names) > 1:
        fail(f"{slug}: zones {', '.join(names)} all start with the site's slug")
    return names[0] if names else None


def _zone_coverage(slug: str, addresses, zones: dict[str, Zone], fail) -> None:
    """netbox_dns writes a record only into a zone that already exists, and skips the rest
    without a word - so a name, or its PTR, with nowhere to go is reported here."""

    def covered(name: str) -> bool:
        return any(name == z or name.endswith(f".{z}") for z in zones)

    for a in addresses:
        if not a.dns_name:
            continue
        if not covered(a.dns_name):
            fail(f"{slug}: {a.dns_name} is in no zone")
        if not covered(a.address.reverse_pointer):
            fail(f"{slug}: {a.dns_name} ({a.address}) has no reverse zone for its PTR")


def _asn(slug: str, role: str, snap: netbox.Snapshot, fail) -> int | None:
    held = sorted(
        raw["asn"]
        for raw in snap.asns
        if _slug(raw, "role") == role and slug in {s["slug"] for s in raw.get("sites") or []}
    )
    if len(held) > 1:
        fail(f"{slug}: ASNs {', '.join(map(str, held))} all carry role {role}")
    return held[0] if held else None


def _gua(slug: str, snap: netbox.Snapshot) -> IPv6Net | None:
    """The widest IPv6 container the site holds - what an ISP delegated it."""
    nets = [
        net
        for p in _scoped(snap.prefixes, slug)
        if p["status"]["value"] == "container"
        and (net := _net(p["prefix"])) is not None
        and net.version == 6
    ]
    return min(nets, key=lambda n: n.prefixlen) if nets else None


def _vlans(slug: str, snap: netbox.Snapshot, fail) -> tuple[dict[str, Vlan], tuple[str, ...]]:
    """A VLAN carries no site of its own when it lives in a group - the group's scope is
    what places it."""
    groups = {g["slug"] for g in _scoped(snap.vlan_groups, slug)}
    declared = {
        v["vid"]: v
        for v in snap.vlans
        if v["status"]["value"] == "active"
        and (_slug(v, "group") in groups or _slug(v, "site") == slug)
    }

    by_vid: dict[int, dict[int, object]] = {}
    for prefix in _scoped(snap.prefixes, slug):
        vlan = prefix.get("vlan")
        if not vlan or prefix["status"]["value"] != "active":
            continue
        net = _net(prefix["prefix"])
        if net is None:
            fail(f"{slug}: {prefix['prefix']!r} is not a network")
            continue
        by_vid.setdefault(vlan["vid"], {})[net.version] = net

    vlans: dict[str, Vlan] = {}
    listen: list[str] = []
    for vid, raw in sorted(declared.items()):
        name = raw["name"]
        nets = by_vid.get(vid)
        if not nets or 4 not in nets:
            fail(f"{slug}/{name}: no active IPv4 prefix on vid {vid}")
            continue
        vlans[name] = Vlan(
            name=name,
            vid=vid,
            ipv4=nets[4],
            ipv6=nets.get(6),
            pool=_pool_for(nets[4], snap),
        )
        if LISTEN_TAG in {t["slug"] for t in raw.get("tags") or []}:
            if vlans[name].ipv6 is None:
                fail(f"{slug}/{name}: IPv6 routes learned here need an IPv6 next hop, "
                     "and it has no IPv6 prefix")
                continue
            listen.append(name)
    return vlans, tuple(sorted(listen))


def _pool_for(net: IPv4Net, snap: netbox.Snapshot) -> tuple[str, str] | None:
    for r in snap.ranges:
        if r["status"]["value"] != "active":
            continue
        start = ipaddress.ip_interface(r["start_address"]).ip
        if start.version == net.version and start in net:
            return str(start), str(ipaddress.ip_interface(r["end_address"]).ip)
    return None


def _pools(slug: str, snap: netbox.Snapshot, communities, fail) -> dict[str, BgpPool]:
    by_purpose: dict[str, dict[int, object]] = {}
    for prefix in _scoped(snap.prefixes, slug):
        purpose = _slug(prefix, "role")
        if purpose not in communities:
            continue
        net = _net(prefix["prefix"])
        if net is None:
            fail(f"{slug}/{purpose}: {prefix['prefix']!r} is not a network")
            continue
        by_purpose.setdefault(purpose, {})[net.version] = net

    pools = {}
    for purpose, nets in sorted(by_purpose.items()):
        if 4 not in nets:
            fail(f"{slug}/{purpose}: no IPv4 prefix carries that role")
            continue
        pools[purpose] = BgpPool(
            purpose=purpose,
            ipv4=nets[4],
            ipv6=nets.get(6),
            speaker_asn=_asn(slug, purpose, snap, fail),
            community=communities[purpose],
        )
    return pools


def _holder(raw: dict) -> str:
    target = raw.get("assigned_object") or {}
    return (target.get("device") or target.get("virtual_machine") or {}).get("name") or ""


def _addresses(snap, vlans, pools) -> tuple[Address, ...]:
    """Every address that falls inside something this site owns.

    NetBox does not scope an IP to a site, so containment is the join - which is also the
    check: an address in no prefix of this site simply is not this site's.
    """
    owned = [v.ipv4 for v in vlans.values()]
    owned += [v.ipv6 for v in vlans.values() if v.ipv6]
    owned += [p.ipv4 for p in pools.values()]
    owned += [p.ipv6 for p in pools.values() if p.ipv6]

    out = []
    for raw in snap.addresses:
        iface = ipaddress.ip_interface(raw["address"])
        if not any(iface.ip in net for net in owned if net.version == iface.ip.version):
            continue
        out.append(
            Address(
                address=iface.ip,
                prefixlen=iface.network.prefixlen,
                dns_name=raw.get("dns_name") or "",
                role=(raw.get("role") or {}).get("value") or "",
                status=raw["status"]["value"],
                holder=_holder(raw),
            )
        )
    return tuple(sorted(out, key=lambda a: (a.address.version, a.address)))


def _in_pool(addresses, pool: BgpPool | None, role: str) -> list[Address]:
    if pool is None:
        return []
    nets = [n for n in (pool.ipv4, pool.ipv6) if n]
    return [
        a
        for a in addresses
        if a.role == role
        and any(a.address in n for n in nets if n.version == a.address.version)
    ]


def _hosts(slug: str, snap: netbox.Snapshot) -> dict[str, Host]:
    return {
        raw["name"]: Host(
            name=raw["name"], role=_slug(raw, "role"), platform=_slug(raw, "platform")
        )
        for raw in [*snap.devices, *snap.virtual_machines]
        if raw.get("name") and _slug(raw, "site") == slug
    }


def _anycast(slug, addresses, pools, fail) -> Anycast | None:
    vips = _in_pool(addresses, pools.get(RESOLVER_PURPOSE), "anycast")
    if not vips:
        return None
    v4 = [a for a in vips if a.address.version == 4]
    v6 = [a for a in vips if a.address.version == 6]
    if len(v4) != 1 or len(v6) != 1 or v4[0].dns_name != v6[0].dns_name:
        fail(f"{slug}: the resolvers' anycast address needs one per family, under one name")
        return None
    return Anycast(fqdn=v4[0].dns_name, ipv4=v4[0].address, ipv6=v6[0].address)


def _vlan_of(address, vlans: dict[str, Vlan]) -> str | None:
    for name, vlan in vlans.items():
        net = vlan.ipv4 if address.version == 4 else vlan.ipv6
        if net is not None and address in net:
            return name
    return None


def _resolvers(slug, hosts, vlans, listen, pools, addresses, fail) -> dict[str, Resolver]:
    """Each resolver's service addresses: the loopbacks it holds in the resolvers' block.

    Services bind to these rather than to the LAN address, because they exist on `lo`
    before the network does. The LAN address only says which VLAN it peers from.
    """
    loopbacks = _in_pool(addresses, pools.get(RESOLVER_PURPOSE), "loopback")
    resolvers = {}
    for host in hosts.values():
        if host.role != RESOLVER_ROLE:
            continue
        held = [a for a in loopbacks if a.holder == host.name]
        v4 = [a for a in held if a.address.version == 4]
        v6 = [a for a in held if a.address.version == 6]
        if len(v4) != 1 or len(v6) != 1:
            fail(
                f"{slug}/{host.name}: a resolver needs one IPv4 and one IPv6 loopback in "
                f"the {RESOLVER_PURPOSE} block, and holds {len(v4)} and {len(v6)}"
            )
            continue
        if not v4[0].dns_name or v4[0].dns_name != v6[0].dns_name:
            fail(f"{slug}/{host.name}: its two service addresses need one DNS name")
            continue

        lan = {
            vlan
            for a in addresses
            if a.holder == host.name and (vlan := _vlan_of(a.address, vlans))
        }
        if len(lan) != 1:
            fail(f"{slug}/{host.name}: a resolver needs its LAN address in exactly one VLAN, "
                 f"not {sorted(lan) or 'none'}")
            continue
        (vlan,) = lan
        if vlan not in listen:
            fail(f"{slug}/{host.name}: peers from {vlan}, where the router isn't listening")
            continue

        resolvers[host.name] = Resolver(
            name=host.name, fqdn=v4[0].dns_name, ipv4=v4[0].address, ipv6=v6[0].address,
            vlan=vlan,
        )
    return resolvers
