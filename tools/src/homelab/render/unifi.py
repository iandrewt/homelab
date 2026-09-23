from .base import HEADER_HASH_FRR, Rendered


def render(repo, ipam) -> Rendered:
    return {
        repo.unifi / f"{site['slug']}-frr.conf": frr_config(site)
        for site in ipam["sites"].values()
        if site["router_asn"] and site["pools"]
    }


def stale(repo, rendered: Rendered) -> list:
    existing = set(repo.unifi.glob("*-frr.conf")) | set(repo.unifi.glob("*-reservations.json"))
    return sorted(existing - set(rendered))


def frr_config(site: dict) -> str:
    # An identifier, not an address to reach - but one the router demonstrably holds
    router_id = min(site["vlans"].values(), key=lambda v: v["vid"])["gateway_ipv4"]
    lines = [
        HEADER_HASH_FRR,
        f"router bgp {site['router_asn']}",
        f" bgp router-id {router_id}",
        " bgp log-neighbor-changes",
        " bgp ebgp-requires-policy",
        " no bgp default ipv4-unicast",
    ]

    groups = []
    for vlan_name in site["listen_vlans"]:
        group = vlan_name.upper()
        groups.append(group)
        lines += [
            " !",
            f" neighbor {group} peer-group",
            f" neighbor {group} remote-as external",
            f" neighbor {group} timers 3 9",
            f" bgp listen range {site['vlans'][vlan_name]['ipv4']} peer-group {group}",
        ]

    for family, suffix in (("ipv4", "V4"), ("ipv6", "V6")):
        lines += [" !", f" address-family {family} unicast"]
        for group in groups:
            lines += [
                f"  neighbor {group} activate",
                f"  neighbor {group} soft-reconfiguration inbound",
                f"  neighbor {group} route-map ADVERTISED-{suffix} in",
                f"  neighbor {group} route-map NOTHING out",
            ]
        lines += ["  maximum-paths 8", " exit-address-family"]
    lines += ["!", "route-map NOTHING deny 10", "exit", "!"]

    for purpose, pool in sorted(site["pools"].items()):
        group = purpose.upper()
        lines += _prefix_list("ip", f"{group}-V4", [pool["ipv4"]])
        lines += _prefix_list("ipv6", f"{group}-V6", [pool["ipv6"]] if pool["ipv6"] else [])
        lines += [
            f"bgp community-list standard {group} permit {pool['community']}",
            "!",
        ]

    for suffix, family in (("V4", "ip"), ("V6", "ipv6")):
        for seq, purpose in enumerate(sorted(site["pools"]), start=1):
            group = purpose.upper()
            lines += [
                f"route-map ADVERTISED-{suffix} permit {seq * 10}",
                f" match {family} address prefix-list {group}-{suffix}",
                f" match community {group}",
                "exit",
                "!",
            ]

    return "\n".join(lines) + "\n"


def _prefix_list(family: str, name: str, blocks: list) -> list[str]:
    """The block a speaker may advertise out of, not the addresses in it."""
    longest = 32 if family == "ip" else 128
    return [
        f"{family} prefix-list {name} seq {seq * 5} permit {block} le {longest}"
        for seq, block in enumerate(blocks, start=1)
    ] + ["!"]
