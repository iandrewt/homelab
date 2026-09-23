import yaml
from conftest import SITE
from homelab.model import load
from homelab.render import seed, unifi


def site_of(snapshot):
    return load(snapshot)["sites"][SITE]


def test_frr_requires_both_the_block_and_the_tag(snapshot):
    """A route has to fall in its purpose's prefix-list *and* carry its community.

    The two have to agree with `frr_bgp`'s `set community` or every advertisement is
    dropped, so the pair being present is the contract worth pinning.
    """
    config = unifi.frr_config(site_of(snapshot))
    assert "match ip address prefix-list DNS-V4" in config
    assert "match community DNS" in config
    assert "bgp community-list standard DNS permit 64512:1" in config


def test_frr_names_no_speaker(snapshot):
    """Adding, replacing or losing a speaker must need no change on the router."""
    config = unifi.frr_config(site_of(snapshot))
    assert "bgp listen range 10.0.110.0/24 peer-group SERVERS" in config
    assert "neighbor SERVERS remote-as external" in config


def ethernet(config: str) -> dict:
    return yaml.safe_load(config)["network"]["ethernets"]["primary"]


def test_an_ordinary_host_takes_its_resolvers_and_search_list_from_the_lease(snapshot):
    """UniFi hands both out; a seed that named them too would be a second place to change
    when either moves - so the seed says nothing about DNS beyond listening to the lease."""
    host = ethernet(seed.network_config(site_of(snapshot)))
    assert set(host) == {"match", "dhcp4", "dhcp6", "emit-lldp",
                         "dhcp4-overrides", "dhcp6-overrides"}
    assert host["dhcp4-overrides"] == {"use-domains": True}


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

