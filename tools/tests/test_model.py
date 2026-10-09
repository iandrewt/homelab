import dataclasses

from conftest import SITE, soa, zone
from homelab.model import load, unzoned


def loads(snapshot, **changes) -> dict:
    return load(dataclasses.replace(snapshot, **changes))


def test_a_site_claims_only_addresses_inside_its_own_prefixes(snapshot):
    """NetBox does not scope an IP to a site, so containment is the whole join."""
    named = loads(snapshot)["sites"][SITE]["named"].values()
    assert "dns.one.test" in named
    assert "elsewhere.example" not in named


def test_a_sites_vlans_are_in_the_group_named_by_its_slug(snapshot):
    """Such a VLAN has no site of its own - matching on one found nothing in live data.
    OpenTofu's netbox-site module joins the same way, and the two have to agree."""
    assert set(loads(snapshot)["sites"][SITE]["vlans"]) == {"management", "servers"}


def test_a_gateway_is_the_routers_address_not_the_first_in_the_prefix(snapshot):
    vlans = loads(snapshot)["sites"][SITE]["vlans"]
    assert vlans["management"]["gateway_ipv4"] == "10.0.10.254"
    assert vlans["servers"]["gateway_ipv6"] == "2001:db8:0:110::1"


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
