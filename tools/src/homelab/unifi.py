"""UniFi's firewall, through Site Manager's connector to each site's gateway.

A stopgap until a provider writes the firewall whole: `apply` takes the runbook's policies a
step at a time, a dry run unless --apply, updating a policy its zone pair already has, by name
or by match, where it differs. A site's console is the one whose serial is its router's in NetBox.
"""

import collections
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import netbox, secrets
from .model import load
from .render import firewall

KEY_REF = "op://Network/UniFi API Key - Site Manager/credential"
SITE_MANAGER = "https://api.ui.com"
# A block matches new flows alone: return traffic is per policy, and so is PMTUD's ICMP
NEW = ["NEW", "INVALID"]
CONSOLE_PORTS = [22, 80, 443]
# UniFi's port reference, 2026-10-30, and what its gear was seen using that it doesn't list
PROTECT_PORTS = [(7441, 7445), 7447, 7550, 7552, 6666, 7888]
UNIFI_UDP_PORTS = [3478, 5514, 10001, 10101]
MATTER_PORT = 5540
EPHEMERAL_PORTS = (32768, 65535)
# Where Matter's devices reach the gateway's controller from: Wi-Fi's own zone, and Thread's
# through People's border routers
GATEWAY_MATTER = ("People", "Devices")
# Where a zone gets what it needs from the gateway, beside ICMP and DHCP
GATEWAY_DNS = ("Work", "Workloads", "Cameras")
GATEWAY_MDNS = ("People", "Devices", "Infrastructure")
# Where a break-glass client sits when it's home, and its server's port, as UniFi has it: the
# API takes no VPN server as a destination
BREAK_GLASS_FROM = ("People",)
BREAK_GLASS_PORT = 51821
# Kept from the internet by a policy, not Internet Access: unticked, that counts every IPv6
# destination outside UniFi's networks as the internet, break-glass and the recursor included
OFFLINE = ("Default", "Network", "Out of Band", "Facilities")


class UnifiError(Exception):
    pass


def register(subparsers) -> None:
    unifi = subparsers.add_parser("unifi", help="the UniFi gateways' firewall, by API", description=__doc__)
    actions = unifi.add_subparsers(dest="action", required=True, title="actions")

    status = actions.add_parser("status", help="Site Magic's tunnels, and each site's Device Isolation")
    status.set_defaults(run=_status)

    export = actions.add_parser("export", help="every site's policies, zones and networks, as JSON")
    export.add_argument("directory", type=Path, help="written as <directory>/<site>/*.json")
    export.set_defaults(run=_export)

    show = actions.add_parser("show", help="a zone pair's policies of ours, in order")
    show.add_argument("site")
    show.add_argument("source", help="a zone's name, as UniFi has it")
    show.add_argument("destination")
    show.set_defaults(run=_show)

    blocked = actions.add_parser("blocked", help="what the firewall blocked, by flow, most first")
    blocked.add_argument("site")
    blocked.add_argument("--minutes", type=int, default=60)
    blocked.add_argument("--top", type=int, default=40)
    blocked.set_defaults(run=_blocked)

    apply = actions.add_parser("apply", help="one step of the runbook's policies")
    apply.add_argument("site")
    apply.add_argument("step", choices=["egress", "ipv6", "home", "gateway", "vpn"])
    apply.add_argument("zone", nargs="?", help="gateway's: the zone whose way to the gateway it is")
    apply.add_argument("--apply", action="store_true", help="make the changes, rather than print them")
    apply.set_defaults(run=_apply)


def _status(args) -> int:
    def run(model, key, consoles):
        by_host = {host: slug for slug, host in consoles.items()}
        for config in _get(key, f"{SITE_MANAGER}/ea/sd-wan-configs"):
            state = _get(key, f"{SITE_MANAGER}/ea/sd-wan-configs/{config['id']}/status")
            for role in ("hubs", "spokes"):
                for site in state[role]:
                    slug = by_host.get(site["hostId"], site["name"])
                    print(f"{role[:-1]} {slug}: {site['applyStatus']}, errors {site['errors']}, "
                          f"warnings {site['warnings']}")
                    for link in site.get("connections", []):
                        hub = by_host.get(link["hubId"].split("_")[0], link["hubId"])
                        for tunnel in link["tunnels"]:
                            print(f"  to {hub}: {tunnel['status']}")
        for slug, host in consoles.items():
            _isolation(key, host, slug, model["sites"][slug])
    return _run(run)


def _isolation(key: str, host: str, slug: str, site: dict) -> None:
    """Proxmox isolates a VNet's guests on one node, and Device Isolation (ACL) between them; only
    the old API has it, a switch setting naming networks by their old IDs."""
    base = f"{SITE_MANAGER}/v1/connector/consoles/{host}/proxy/network/api/s/default/rest"
    try:
        networks = {n["_id"]: n for n in _get(key, f"{base}/networkconf")}
        switch = next(s for s in _get(key, f"{base}/setting") if s["key"] == "global_switch")
    except UnifiError as e:
        print(f"isolation {slug}: unread, {e}")
        return
    isolated = {networks[i].get("vlan") for i in switch.get("acl_device_isolation", []) if i in networks}
    tagged = {v["vid"] for v in site["vlans"].values() if "isolate-ports" in v["tags"]}
    names = {v["vid"]: n for n, v in site["vlans"].items()}
    for vid in sorted(tagged - isolated):
        print(f"isolation {slug}: {names[vid]} ({vid}) is tagged isolate-ports, but Device Isolation is off")
    for vid in sorted(isolated - tagged, key=str):
        print(f"isolation {slug}: {names.get(vid, 'untagged')} ({vid}) has Device Isolation, but no isolate-ports tag")
    if tagged == isolated:
        print(f"isolation {slug}: {len(tagged)} networks, as tagged")


def _export(args) -> int:
    def run(model, key, consoles):
        for slug, host in consoles.items():
            c = Console(key, host)
            out = args.directory / slug
            out.mkdir(parents=True, exist_ok=True)
            networks = [c.call("GET", f"{c.site}/networks/{n['id']}") for n in c.every("networks")]
            for name, data in {
                "firewall-policies": c.policies, "firewall-zones": list(c.zones.values()), "networks": networks,
            }.items():
                (out / f"{name}.json").write_text(json.dumps(data, indent=1) + "\n")
                print(f"{slug} {name}: {len(data)}")
    return _run(run)


def _show(args) -> int:
    def run(model, key, consoles):
        c = Console(key, consoles[args.site])
        for p in c.ordered(args.source, args.destination):
            print(f"  {p['action']['type']:6} {p['name']}")
    return _run(run)


def _blocked(args) -> int:
    """A reply is shown by the port it came from, since the one it went to is ephemeral."""
    def run(model, key, consoles):
        url = f"{SITE_MANAGER}/v1/connector/consoles/{consoles[args.site]}/proxy/network/v2/api/site/default/traffic-flows"
        until = int(time.time() * 1000)
        flows, page = [], 0
        while True:
            body = {"timestampFrom": until - args.minutes * 60_000, "timestampTo": until,
                    "pageNumber": page, "pageSize": 1000, "action": ["blocked"]}
            got = _call(key, "POST", url, body)
            flows += got["data"]
            page += 1
            if not got.get("has_next"):
                break
        seen = collections.Counter()
        for f in flows:
            src, dst = f["source"], f["destination"]
            port = dst.get("port")
            if port and port >= 32768:
                port = f"from {src.get('port')}"
            policy = ", ".join(p.get("name") or p.get("internal_type", "?") for p in f.get("policies", []))
            seen[(src.get("network_name") or src.get("zone_name") or "internet",
                  dst.get("network_name") or dst.get("zone_name") or "internet",
                  f.get("protocol"), port or "", policy)] += f.get("count", 1)
        print(f"{sum(seen.values())} blocked in {args.minutes} minutes")
        for (src, dst, protocol, port, policy), n in seen.most_common(args.top):
            print(f"{n:7}  {src} -> {dst}  {protocol} {port}  {policy}")
    return _run(run)


def _apply(args) -> int:
    def run(model, key, consoles):
        if args.step == "gateway" and not args.zone:
            raise UnifiError("gateway takes a zone")
        c = Console(key, consoles[args.site])
        site = model["sites"][args.site]
        steps = {"egress": egress, "ipv6": ipv6, "home": home, "gateway": gateway, "vpn": vpn}
        wanted = steps[args.step](c, site, model["sites"], args.zone)
        c.ensure(wanted, apply=args.apply)
    return _run(run)


def _run(action) -> int:
    try:
        model = load()
        key = secrets.resolve_one(KEY_REF)
        consoles = _consoles(key, model)
        action(model, key, consoles)
    except (netbox.Unreachable, secrets.SecretError, UnifiError, KeyError) as bad:
        print(f"FAIL: {bad}", file=sys.stderr)
        return 1
    return 0


def _consoles(key: str, model: dict) -> dict[str, str]:
    """Each site's console, by its router's serial."""
    serials = {s["router_serial"].lower(): slug for slug, s in model["sites"].items() if s["router_serial"]}
    return {
        serials[serial]: host["id"]
        for host in _get(key, f"{SITE_MANAGER}/v1/hosts")
        if (serial := ((host.get("reportedState") or {}).get("hardware") or {}).get("serialno", "").lower())
        in serials
    }


def _call(key: str, method: str, url: str, body=None):
    request = urllib.request.Request(
        url, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"X-API-KEY": key, "Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            raw = response.read()
    except urllib.error.HTTPError as e:
        raise UnifiError(f"{method} {url.split('/integration')[-1]}: {e.code} {e.read()[:500].decode()}") from e
    except urllib.error.URLError as e:
        raise UnifiError(f"{method} {url}: {e.reason}") from e
    return json.loads(raw) if raw else None


def _get(key: str, url: str):
    return _call(key, "GET", url)["data"]


class Console:
    """One site's Network application, through the connector."""

    def __init__(self, key: str, host: str) -> None:
        self.key = key
        base = f"{SITE_MANAGER}/v1/connector/consoles/{host}/proxy/network/integration/v1"
        self.site = f"{base}/sites/{_get(key, f'{base}/sites')[0]['id']}"
        self.zones = {z["name"]: z for z in self.every("firewall/zones")}
        self.networks = {n["name"]: n["id"] for n in self.every("networks")}
        self.policies = self.every("firewall/policies")

    def call(self, method: str, url: str, body=None):
        return _call(self.key, method, url, body)

    def every(self, path: str) -> list:
        items, offset = [], 0
        while True:
            page = self.call("GET", f"{self.site}/{path}?offset={offset}&limit=200")
            items += page["data"]
            offset += page["count"]
            if offset >= page["totalCount"] or not page["count"]:
                return items

    def zone(self, name: str) -> str:
        return self.zones[name]["id"]

    def ordering(self, source: str, destination: str) -> str:
        return (f"{self.site}/firewall/policies/ordering?sourceFirewallZoneId={self.zone(source)}"
                f"&destinationFirewallZoneId={self.zone(destination)}")

    def ordered(self, source: str, destination: str) -> list[dict]:
        ids = self.call("GET", self.ordering(source, destination))["orderedFirewallPolicyIds"]
        by_id = {p["id"]: p for p in self.policies}
        return [by_id[i] for i in ids["beforeSystemDefined"] + ids["afterSystemDefined"]]

    def existing(self, body: dict) -> dict | None:
        def same(p: dict) -> bool:
            return all(p.get(k) == body.get(k) for k in ("source", "destination", "ipProtocolScope")) \
                and p["action"]["type"] == body["action"]["type"]

        return next((p for p in self.policies if p["metadata"]["origin"] == "USER_DEFINED"
                     and p["source"]["zoneId"] == body["source"]["zoneId"]
                     and p["destination"]["zoneId"] == body["destination"]["zoneId"]
                     and (p["name"] == body["name"] or same(p))), None)

    def ensure(self, bodies: list[dict], *, apply: bool) -> None:
        names = {z["id"]: n for n, z in self.zones.items()}
        pairs = set()
        try:
            for body in bodies:
                pair = (names[body["source"]["zoneId"]], names[body["destination"]["zoneId"]])
                held = self.existing(body)
                if held and _canonical({k: held.get(k) for k in body}) == _canonical(body):
                    print(f"  exists   {pair[0]} -> {pair[1]}: {held['name']}")
                    continue
                verb = "update" if held else "create"
                if not apply:
                    print(f"  would {verb} {pair[0]} -> {pair[1]}: {body['name']}\n           {json.dumps(body)}")
                    continue
                if held:
                    made = self.call("PUT", f"{self.site}/firewall/policies/{held['id']}", body)
                    self.policies = [made if p["id"] == held["id"] else p for p in self.policies]
                else:
                    made = self.call("POST", f"{self.site}/firewall/policies", body)
                    self.policies.append(made)
                pairs.add(pair)
                print(f"  {verb}d  {pair[0]} -> {pair[1]}: {body['name']}")
        finally:
            # A created allow is below the pair's block until then
            for pair in sorted(pairs):
                self.settle(*pair)

    def settle(self, source: str, destination: str) -> None:
        """A policy is created at the end of its pair, so one added after a block moves above it."""
        url = self.ordering(source, destination)
        ids = self.call("GET", url)["orderedFirewallPolicyIds"]
        action = {p["id"]: p["action"]["type"] for p in self.policies}
        before = ids["beforeSystemDefined"]
        wanted = sorted(before, key=lambda i: action.get(i) != "ALLOW")
        if wanted != before:
            self.call("PUT", url, {"orderedFirewallPolicyIds": {
                "beforeSystemDefined": wanted, "afterSystemDefined": ids["afterSystemDefined"]}})
            print(f"  reordered {source} -> {destination}: allows first")


def _canonical(value):
    """The API returns a list in its own order."""
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, list):
        return sorted((_canonical(v) for v in value), key=json.dumps)
    return value


def ports(*spec) -> dict:
    items = [{"type": "PORT_NUMBER_RANGE", "start": p[0], "stop": p[1]} if isinstance(p, tuple)
             else {"type": "PORT_NUMBER", "value": p} for p in spec]
    return {"type": "PORTS", "matchOpposite": False, "items": items}


def scope(protocol: str | None, version: str = "IPV4_AND_IPV6") -> dict:
    if protocol is None:
        return {"ipVersion": version}
    if protocol == "TCP_UDP":
        return {"ipVersion": version, "protocolFilter": {"type": "PRESET", "preset": {"name": "TCP_UDP"}}}
    return {"ipVersion": version,
            "protocolFilter": {"type": "NAMED_PROTOCOL", "protocol": {"name": protocol}, "matchOpposite": False}}


def side(c: Console, zone: str, *, networks=None, addresses=None, port=None) -> dict:
    out = {"zoneId": c.zone(zone)}
    match = {}
    if networks:
        match = {"type": "NETWORK", "networkFilter": {"networkIds": [c.networks[n] for n in networks],
                                                      "matchOpposite": False}}
    elif addresses:
        match = {"type": "IP_ADDRESS", "ipAddressFilter": {"type": "IP_ADDRESSES", "matchOpposite": False, "items": [
            {"type": "SUBNET" if "/" in a else "IP_ADDRESS", "value": a} for a in addresses]}}
    if port:
        match = {**match, "type": match.get("type", "PORT"), "portFilter": port}
    if match:
        out["trafficFilter"] = match
    return out


def policy(c: Console, name: str, action: str, source: dict, destination: dict, ip: dict, *,
           states=None, log=False) -> dict:
    body = {"enabled": True, "name": name, "action": {"type": action}, "source": source,
            "destination": destination, "ipProtocolScope": ip, "loggingEnabled": log}
    if action == "ALLOW":
        # External's and the Gateway's replies are UniFi's own, and the API refuses a policy's
        body["action"]["allowReturnTraffic"] = destination["zoneId"] not in (c.zone("External"), c.zone("Gateway"))
    if states:
        body["connectionStateFilter"] = states
    return body


def egress(c: Console, site: dict, sites: dict, zone=None) -> list[dict]:
    """29 and 30. 33 is Cameras' Internet Access, unticked."""
    offline = [n for n in OFFLINE if n in c.networks]
    out = []
    if timed := [n for n in ("Network", "Facilities") if n in c.networks]:
        out.append(policy(c, "Allow Appliances NTP", "ALLOW", side(c, "Appliances", networks=timed),
                          side(c, "External", port=ports(123)), scope("UDP")))
    if offline:
        out.append(policy(c, "Block Appliances Internet", "BLOCK", side(c, "Appliances", networks=offline),
                          side(c, "External"), scope(None), states=NEW))
    return out


def ipv6(c: Console, site: dict, sites: dict, zone=None) -> list[dict]:
    """34, from each zone holding a network."""
    out = []
    for name, z in c.zones.items():
        if name in ("External", "Gateway", "Vpn", "Internal") or not z.get("networkIds"):
            continue
        for action, to in firewall.cross_site_ipv6(site, sites):
            others = " ".join(sorted({slug.title() for _, slug in to}))
            label = f"{action} {others} IPv6" + (" Internet" if action == "Allow" else "")
            out.append(policy(c, label, action.upper(), side(c, name), side(c, "External", addresses=[p for p, _ in to]),
                              scope(None, "IPV6"), states=NEW if action == "Reject" else None))
    return out


def home(c: Console, site: dict, sites: dict, zone=None) -> list[dict]:
    """24 and 24a: People reaches Devices, and Matter's devices report back."""
    return [
        policy(c, "Allow People to Devices", "ALLOW", side(c, "People"), side(c, "Devices"), scope(None)),
        # A subscription's interval outlives conntrack's, so its reports arrive as NEW
        policy(c, "Allow Matter Reports", "ALLOW", side(c, "Devices", port=ports(MATTER_PORT)),
               side(c, "People", port=ports(EPHEMERAL_PORTS)), scope("UDP")),
    ]


def gateway(c: Console, site: dict, sites: dict, zone: str) -> list[dict]:
    """50-69: a zone's way to the gateway, then its block."""
    gw = side(c, "Gateway")
    src = side(c, zone)
    out = [
        policy(c, "Allow Gateway ICMP", "ALLOW", src, gw, scope("ICMP", "IPV4")),
        policy(c, "Allow Gateway ICMPv6", "ALLOW", src, gw, scope("ICMPV6", "IPV6")),
    ]
    if zone != "Vpn":
        out.append(policy(c, "Allow Gateway DHCP", "ALLOW", src, side(c, "Gateway", port=ports(67)), scope("UDP", "IPV4")))
    if zone in GATEWAY_DNS:
        out.append(policy(c, "Allow Gateway DNS", "ALLOW", src, side(c, "Gateway", port=ports(53)), scope("TCP_UDP")))
    if zone in GATEWAY_MDNS:
        out += [
            policy(c, "Allow Gateway mDNS", "ALLOW", src, side(c, "Gateway", port=ports(5353)), scope("UDP")),
            # The gateway asks from an ephemeral port, and a reply to a multicast query is NEW
            policy(c, "Allow Gateway mDNS Replies", "ALLOW", side(c, zone, port=ports(5353)), gw, scope("UDP")),
        ]
    if zone in GATEWAY_MATTER:
        out.append(policy(c, "Allow Gateway Matter Reports", "ALLOW", side(c, zone, port=ports(MATTER_PORT)),
                          side(c, "Gateway", port=ports(EPHEMERAL_PORTS)), scope("UDP")))
    if zone in BREAK_GLASS_FROM and any(v["type"] == "WIREGUARD" for v in c.every("vpn/servers")):
        out.append(policy(c, "Allow Break-glass", "ALLOW", src, side(c, "Gateway", port=ports(BREAK_GLASS_PORT)),
                          scope("UDP")))
    listening = sorted({r for router in site["routers"].values() for g in router["peer_groups"] for r in g["ranges"]})
    if zone == "Workloads" and listening:
        out.append(policy(c, "Allow Gateway BGP", "ALLOW", side(c, zone, addresses=listening),
                          side(c, "Gateway", port=ports(179)), scope("TCP")))
    if zone == "Appliances":
        unifi = side(c, zone, networks=["Network"])
        out += [
            policy(c, "Allow Gateway Inform", "ALLOW", unifi, side(c, "Gateway", port=ports(8080)), scope("TCP")),
            policy(c, "Allow Gateway STUN, Syslog and Discovery", "ALLOW", unifi,
                   side(c, "Gateway", port=ports(*UNIFI_UDP_PORTS)), scope("UDP")),
            policy(c, "Allow Repair Port Console", "ALLOW", side(c, zone, networks=["Default"]),
                   side(c, "Gateway", port=ports(*CONSOLE_PORTS)), scope("TCP"), log=True),
        ]
    if zone == "Cameras":
        out += [
            policy(c, "Allow Gateway Protect", "ALLOW", src, side(c, "Gateway", port=ports(*PROTECT_PORTS)), scope("TCP")),
            policy(c, "Allow Gateway Protect Discovery", "ALLOW", src, side(c, "Gateway", port=ports(10001)),
                   scope("UDP")),
        ]
    if zone == "Vpn":
        # Site Magic's OSPF runs between the tunnel's own addresses, outside every site's
        # ranges, so the block names the other sites alone and break-glass keeps the console
        out.append(policy(c, "Allow Gateway OSPF", "ALLOW", src, gw, scope("OSPF", "IPV4")))
        src = side(c, zone, addresses=[r for s in sites.values() if s["slug"] != site["slug"] for r in s["ranges"]])
    out.append(policy(c, "Block Gateway", "BLOCK", src, gw, scope(None), states=NEW))
    return out


def vpn(c: Console, site: dict, sites: dict, zone=None) -> list[dict]:
    """31, and VPN's way to the gateway (57, 69)."""
    return [
        policy(c, "Block VPN to Guests", "BLOCK", side(c, "Vpn"), side(c, "Hotspot"), scope(None), states=NEW),
        *gateway(c, site, sites, "Vpn"),
    ]
