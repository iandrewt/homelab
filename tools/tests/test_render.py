import yaml
from conftest import SITE
from homelab.model import load
from homelab.render import seed, unifi


def site_of(snapshot):
    return load(snapshot).site(SITE)


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
    assert set(host) == {"match", "dhcp4", "dhcp6", "ipv6-address-generation",
                         "dhcp4-overrides", "dhcp6-overrides"}
    assert host["dhcp4-overrides"] == {"use-domains": True}


def test_a_resolver_asks_itself_then_its_gateway_never_the_vip(snapshot):
    """Nothing a resolver needs to build itself may come from the VIP it will serve."""
    site = site_of(snapshot)
    resolver = ethernet(seed.network_config(site, resolver=site.resolvers["ratatoskr"]))
    assert resolver["nameservers"]["addresses"] == ["127.0.0.1", "10.0.110.1", "2001:db8:0:110::1"]
    # networkd rejects the file outright when the two disagree
    assert resolver["dhcp4-overrides"] == resolver["dhcp6-overrides"] == {
        "use-domains": True, "use-dns": False
    }


def test_the_seed_asks_for_the_address_netbox_records(snapshot):
    """NetworkManager picks stable-privacy unless told, which changes on every reinstall."""
    assert "ipv6-address-generation: eui64" in seed.network_config(site_of(snapshot))


def test_the_seed_carries_no_address_of_its_own(snapshot):
    config = seed.network_config(site_of(snapshot))
    assert "addresses:" not in config.split("nameservers:")[0]


def test_a_resolvers_own_service_address_is_distinguishable_from_the_vip(snapshot):
    """The VIP says where DNS is; a resolver's own address says which one answered.

    Both live in the `dns` prefix, so only the IP role tells them apart - which is why
    the model exposes them by role rather than by address.
    """
    site = site_of(snapshot)
    assert [str(a.address) for a in site.in_pool("dns", "anycast")] == [
        "10.0.200.1", "2001:db8::53"
    ]
    assert [str(a.address) for a in site.in_pool("dns", "loopback")] == [
        "10.0.200.2", "2001:db8::2"
    ]
