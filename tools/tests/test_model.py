import dataclasses

import pytest
from conftest import SITE, prefix
from homelab.model import InvalidSource, load


def loads(snapshot, **changes):
    return load(dataclasses.replace(snapshot, **changes))


def errors(snapshot, **changes) -> str:
    with pytest.raises(InvalidSource) as raised:
        loads(snapshot, **changes)
    return str(raised.value)


def test_a_site_claims_only_addresses_inside_its_own_prefixes(snapshot):
    """NetBox does not scope an IP to a site, so containment is the whole join."""
    held = {a.dns_name for a in loads(snapshot).site(SITE).addresses}
    assert "elsewhere.example" not in held
    assert "dns.one.test" in held


def test_a_vlan_in_a_group_belongs_to_the_site_the_group_is_scoped_to(snapshot):
    """Such a VLAN has no site of its own - matching on one found nothing in live data."""
    assert set(loads(snapshot).site(SITE).vlans) == {"management", "servers"}


def test_a_vlan_with_no_ipv4_prefix_is_an_error(snapshot):
    only_v6 = [p for p in snapshot.prefixes if ":" in p["prefix"] or not p["vlan"]]
    assert "no active IPv4 prefix on vid 110" in errors(snapshot, prefixes=only_v6)


def test_a_prefix_that_is_not_a_network_is_reported_not_raised(snapshot):
    broken = [*snapshot.prefixes, prefix("10.0.0.999/24", vid=110)]
    assert "is not a network" in errors(snapshot, prefixes=broken)


def test_a_resolver_needs_a_service_address_in_each_family(snapshot):
    """What it binds and what FRR originates - one missing and it serves half the site."""
    v4_only = [a for a in snapshot.addresses if a["address"] != "2001:db8::2/64"]
    assert "holds 1 and 0" in errors(snapshot, addresses=v4_only)


def test_a_name_with_no_reverse_zone_is_an_error(snapshot):
    """netbox_dns skips a PTR with nowhere to go in silence"""
    typo = [z for z in snapshot.zones if z["name"] != "10.in-addr.arpa"]
    assert "has no reverse zone for its PTR" in errors(snapshot, zones=typo)


def test_a_public_copy_of_a_zone_is_not_the_one_the_resolvers_serve(snapshot):
    """Stage 2 of the DNS plan puts a copy of each internal zone, under the same name, in
    the `public` view for Route53."""
    from conftest import soa, zone

    public = {**zone("one.test", view="public"), "nameservers": [{"name": "ns-1.example"}]}
    zones = loads(snapshot, zones=[*snapshot.zones, public],
                  records=[*snapshot.records, soa("one.test", view="public")]).zones
    assert zones["one.test"].nameservers == ("ratatoskr-dns.one.test",)


def test_a_resolver_must_peer_from_a_vlan_the_router_listens_on(snapshot):
    """Its session would simply never come up, and nothing else would say why."""
    deaf = [{**v, "tags": []} for v in snapshot.vlans]
    assert "where the router isn't listening" in errors(snapshot, vlans=deaf)


def test_a_reserved_range_is_not_mistaken_for_the_dhcp_pool(snapshot):
    from conftest import ip_range

    ranges = [ip_range("10.0.110.2/24", "10.0.110.15/24", status="reserved")]
    assert loads(snapshot, ranges=ranges).site(SITE).vlan("servers").pool is None
