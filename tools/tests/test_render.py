import yaml
from conftest import SITE
from homelab.model import load
from homelab.render import frr, seed


def site_of(snapshot):
    return load(snapshot)["sites"][SITE]


def test_frr_requires_both_the_block_and_the_tag(snapshot):
    """A route has to fall in its purpose's prefix-list *and* carry its community.

    The two have to agree with `frr_bgp`'s `set community` or every advertisement is
    dropped, so the pair being present is the contract worth pinning.
    """
    config = frr.frr_config(site_of(snapshot))
    assert "match ip address prefix-list DNS-V4" in config
    assert "match community DNS" in config
    assert "bgp community-list standard DNS permit 64512:1" in config


def test_ipv6_reaches_the_router_with_a_next_hop_it_can_install(snapshot):
    """GoBGP sends IPv6 routes over an IPv4 session with an IPv4-mapped next hop, which the
    UDM's kernel rejects. So a listen VLAN has an IPv6 session that carries IPv6 alone, and
    a mapped next hop is denied before any purpose's permit - from either session.
    """
    config = frr.frr_config(site_of(snapshot))
    ipv4 = config.split(" address-family ipv4 unicast")[1].split(" exit-address-family")[0]
    ipv6 = config.split(" address-family ipv6 unicast")[1].split(" exit-address-family")[0]

    assert " bgp listen range 2001:db8:0:110::/64 peer-group SERVERS-V6" in config
    assert "neighbor SERVERS-V6 activate" in ipv6
    assert "SERVERS-V6" not in ipv4
    assert "route-map ADVERTISED-V6 deny 5\n match ipv6 next-hop prefix-list IPV4-MAPPED" in config


def ethernet(config: str) -> dict:
    return yaml.safe_load(config)["network"]["ethernets"]["primary"]


def test_both_families_override_alike(snapshot):
    """networkd shares one [DHCP] section between them, so netplan rejects the whole file
    when the two disagree - and a missing one counts as disagreeing."""
    site = site_of(snapshot)
    for resolver in (None, site["resolvers"]["ratatoskr"]):
        host = ethernet(seed.network_config(site, resolver=resolver))
        assert host["dhcp4-overrides"] == host["dhcp6-overrides"]


def test_a_resolver_asks_its_gateway_never_the_vip(snapshot):
    """Nothing a resolver needs to build itself may come from the VIP it will serve, and
    the lease will hand out the VIP by DHCP and RA alike."""
    site = site_of(snapshot)
    resolver = ethernet(seed.network_config(site, resolver=site["resolvers"]["ratatoskr"]))
    assert resolver["nameservers"]["addresses"] == ["10.0.110.1", "2001:db8:0:110::1"]
    assert resolver["dhcp4-overrides"] == {"use-domains": True, "use-dns": False}
    assert resolver["ra-overrides"] == {"use-dns": False}

