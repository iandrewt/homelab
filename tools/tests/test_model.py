import dataclasses

from conftest import DNS_ONE, DNS_TWO, OTHER, SITE, SPOKE, device, prefix, soa, vlan, zone
from homelab import netbox
from homelab.model import load, sources, unzoned


def loads(snapshot, **changes) -> dict:
    return load(dataclasses.replace(snapshot, **changes))


def test_a_site_claims_only_addresses_inside_its_own_prefixes(snapshot):
    """NetBox does not scope an IP to a site, so containment is the whole join."""
    named = loads(snapshot)["sites"][SITE]["named"].values()
    assert "ratatoskr-dns.one.test" in named
    assert "munin-dns.two.test" not in named
    assert "elsewhere.example" not in named


def test_a_global_address_belongs_to_every_site_whose_fleet_originates_it(snapshot):
    """The recursor sits in no site's prefixes. It's each site's because each site's fleet
    is among its origins - and so each site's router accepts it from its own fleet."""
    sites = loads(snapshot)["sites"]
    for slug, fleet in ((SITE, DNS_ONE), (OTHER, DNS_TWO)):
        assert sites[slug]["dns"]["recursor"] == {"ipv4": "10.239.0.53", "ipv6": "2001:db8::53"}
        assert "10.239.0.53" in sites[slug]["speakers"][fleet]["addresses"]


def test_a_spoke_with_no_fleet_of_its_own_still_has_the_recursor(snapshot):
    """It's global: Site Magic carries it from whichever hub's fleet is nearer."""
    dns = loads(snapshot)["sites"][SPOKE]["dns"]
    assert dns["recursor"]["ipv4"] == "10.239.0.53"
    assert dns["asn"] is None


def test_a_sites_vlans_are_in_the_group_named_by_its_slug(snapshot):
    """Such a VLAN has no site of its own - matching on one found nothing in live data.
    OpenTofu's netbox-site module joins the same way, and the two have to agree."""
    assert set(loads(snapshot)["sites"][SITE]["vlans"]) == {"network", "san", "servers", "k8s-1"}


def test_a_gateway_is_the_routers_address_not_the_first_in_the_prefix(snapshot):
    vlans = loads(snapshot)["sites"][SITE]["vlans"]
    assert vlans["network"]["gateway_ipv4"] == "10.0.0.65"
    assert vlans["servers"]["gateway_ipv6"] == "2001:db8:1:1300::1"


def test_a_vlan_only_network_has_no_gateway_and_may_have_no_ipv4(snapshot):
    """UniFi's VLAN-only networks have no router interface, and san is IPv6-only."""
    san = loads(snapshot)["sites"][SITE]["vlans"]["san"]
    assert (san["ipv4"], san["gateway_ipv4"], san["gateway_ipv6"]) == (None, None, None)
    assert san["ipv6"] == "2001:db8:1:1210::/64"


def test_trust_collects_tagged_sub_pools_and_vlans_alike(snapshot):
    """A firewall rule names a tag; whatever carries it is trusted, VLAN or pod pool."""
    trusted = loads(snapshot)["sites"][SITE]["trusted"]
    assert trusted["proxmox-api"] == ["10.0.132.0/27"]
    assert trusted["dns-api"] == ["10.0.30.0/24", "2001:db8:1:1300::/64"]


def test_a_public_copy_of_a_zone_is_not_the_one_the_resolvers_serve(snapshot):
    """Stage 2 of the DNS plan puts a copy of each internal zone, under the same name, in
    the `public` view for Route53."""
    public = {**zone("one.test", view="public"), "nameservers": [{"name": "ns-1.example"}]}
    zones = loads(snapshot, zones=[*snapshot.zones, public],
                  records=[*snapshot.records, soa("one.test", view="public")])["zones"]
    assert zones["one.test"]["nameservers"] == ["ratatoskr-dns.one.test"]


def test_a_name_with_no_reverse_zone_is_reported(snapshot):
    """netbox_dns skips a PTR with nowhere to go without a word"""
    typo = [z for z in snapshot.zones if z["name"] != "10.in-addr.arpa"]
    assert any("no reverse zone" in p for p in unzoned(loads(snapshot, zones=typo)))


def test_an_audience_is_its_zones_at_its_sites_and_its_tags_anywhere():
    """A VLAN's zone is its prefixes'; a site-scoped prefix counts only at the audience's
    sites, a global one at all of them; a host's tag covers its primary address alone."""
    snap = netbox.Snapshot(
        vlans=[vlan(20, "trusted", zone="people"), vlan(20, "trusted", group=OTHER, zone="people")],
        prefixes=[
            prefix("10.0.2.0/25", vid=20),
            prefix("10.1.2.0/25", site_slug=OTHER, vid=20),
            prefix("10.9.0.0/21", site_slug=None, zone="people"),
        ],
        devices=[device("hugin", "dns-resolver", tags=["connector"], primary_ip6="2001:db8::7/64")],
    )
    found = sources({"zones": ["people"], "tags": ["connector"], "sites": [SITE]}, snap)

    assert found["ipv4"] == ["10.0.2.0/25", "10.9.0.0/21"]
    assert found["ipv6"] == ["2001:db8::7/128"]
