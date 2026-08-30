"""Pure functions turning a site index / VLAN ID into real CIDRs. No state, no I/O --
importable from generate.py, validate.py, or a REPL.

Per site, per family:
  - IPv4: site N gets the Nth /16 out of ipv4_supernet. A VLAN's third octet is its ID,
    so its /24 is the Nth-by-VLAN-ID /24 inside that /16. The last /20 is reserved for
    BGP-advertised addresses (not a VLAN) -- that's why VLAN IDs must stay below 240.
  - IPv6 ULA: site N gets the Nth /48 out of ipv6_ula_supernet. A VLAN's /64 is that /48
    with the VLAN ID spliced in as the 4th hextet's literal text (VLAN 40 ->
    ...:40::/64)
  - IPv6 GUA: UniFi assigns each VLAN's /64 itself, sequentially by creation order -
    not derivable, not handled here.
  - The BGP-reserved pool (both families) is the *last* subnet of that size within the
    site's own supernet - last /20 of the /16 for IPv4, last /56 of the /48 for IPv6.
"""
import ipaddress

# Last /20 of each site's IPv4 /16 -> VLAN IDs must stay in 0-239.
BGP_IPV4_PREFIXLEN = 20
BGP_IPV4_RESERVED_FROM = 256 - 2 ** (24 - BGP_IPV4_PREFIXLEN)  # 240

# Last /56 of each site's IPv6 ULA /48.
BGP_IPV6_PREFIXLEN = 56


def site_ipv4_supernet(ipv4_supernet: str, index: int) -> ipaddress.IPv4Network:
    base = ipaddress.ip_network(ipv4_supernet)
    return next(n for i, n in enumerate(base.subnets(new_prefix=16)) if i == index)


def site_ipv6_supernet(ipv6_ula_supernet: str, index: int) -> ipaddress.IPv6Network:
    base = ipaddress.ip_network(ipv6_ula_supernet)
    return next(n for i, n in enumerate(base.subnets(new_prefix=48)) if i == index)


def site_bgp_ipv4_pool(site_ipv4_16: ipaddress.IPv4Network) -> ipaddress.IPv4Network:
    if site_ipv4_16.prefixlen != 16:
        raise ValueError(f"expected a /16, got {site_ipv4_16}")
    return list(site_ipv4_16.subnets(new_prefix=BGP_IPV4_PREFIXLEN))[-1]


def site_bgp_ipv6_pool(site_ipv6_48: ipaddress.IPv6Network) -> ipaddress.IPv6Network:
    if site_ipv6_48.prefixlen != 48:
        raise ValueError(f"expected a /48, got {site_ipv6_48}")
    return list(site_ipv6_48.subnets(new_prefix=BGP_IPV6_PREFIXLEN))[-1]


def vlan_ipv4_cidr(site_ipv4_16: ipaddress.IPv4Network, vlan_id: int) -> ipaddress.IPv4Network:
    if site_ipv4_16.prefixlen != 16:
        raise ValueError(f"expected a /16, got {site_ipv4_16}")
    if not 0 <= vlan_id < BGP_IPV4_RESERVED_FROM:
        raise ValueError(f"VLAN ID {vlan_id} must be below {BGP_IPV4_RESERVED_FROM} (reserved for BGP)")
    return next(n for i, n in enumerate(site_ipv4_16.subnets(new_prefix=24)) if i == vlan_id)


def vlan_ipv6_ula_cidr(site_ipv6_48: ipaddress.IPv6Network, vlan_id: int) -> ipaddress.IPv6Network:
    if site_ipv6_48.prefixlen != 48:
        raise ValueError(f"expected a /48, got {site_ipv6_48}")
    if not 0 <= vlan_id <= 9999:
        raise ValueError(f"VLAN ID {vlan_id} doesn't fit as a literal 4-digit hextet")
    prefix_hextets = ":".join(site_ipv6_48.network_address.exploded.split(":")[:3])
    return ipaddress.ip_network(f"{prefix_hextets}:{vlan_id}::/64")
