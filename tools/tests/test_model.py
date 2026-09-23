import dataclasses

from conftest import SITE, dnssec_policy, soa, zone
from homelab.model import load, unzoned


def loads(snapshot, **changes) -> dict:
    return load(dataclasses.replace(snapshot, **changes))


def test_a_site_claims_only_addresses_inside_its_own_prefixes(snapshot):
    """NetBox does not scope an IP to a site, so containment is the whole join."""
    named = loads(snapshot)["sites"][SITE]["named"].values()
    assert "dns.one.test" in named
    assert "elsewhere.example" not in named


def test_a_vlan_in_a_group_belongs_to_the_site_the_group_is_scoped_to(snapshot):
    """Such a VLAN has no site of its own - matching on one found nothing in live data."""
    assert set(loads(snapshot)["sites"][SITE]["vlans"]) == {"management", "servers"}


def test_a_resolvers_service_address_is_told_from_the_vip_by_its_role(snapshot):
    """Both sit in the `dns` block; only the IP role says which is which. The resolver
    peers from the VLAN its LAN address is in."""
    site = loads(snapshot)["sites"][SITE]
    assert site["anycast"] == {"ipv4": "10.0.200.1", "ipv6": "2001:db8::53"}
    assert site["resolvers"]["ratatoskr"] == {
        "ipv4": "10.0.200.2", "ipv6": "2001:db8::2", "vlan": "servers"
    }


def test_a_public_copy_of_a_zone_is_not_the_one_the_resolvers_serve(snapshot):
    """Stage 2 of the DNS plan puts a copy of each internal zone, under the same name, in
    the `public` view for Route53."""
    public = {**zone("one.test", view="public"), "nameservers": [{"name": "ns-1.example"}]}
    zones = loads(snapshot, zones=[*snapshot.zones, public],
                  records=[*snapshot.records, soa("one.test", view="public")])["zones"]
    assert zones["one.test"]["nameservers"] == ["ratatoskr-dns.one.test"]


def test_a_zone_is_signed_with_its_policys_key_templates(snapshot):
    """Found by name - a zone nests only its policy's name - and in the lowercase key types
    PowerDNS's API takes. A zone without a policy is unsigned."""
    one, *rest = snapshot.zones
    zones = loads(snapshot, zones=[{**one, "dnssec_policy": {"name": "split"}}, *rest],
                  dnssec_policies=[dnssec_policy("split", "ZSK", "KSK")])["zones"]
    assert [k["keytype"] for k in zones["one.test"]["dnssec_keys"]] == ["ksk", "zsk"]
    assert zones["10.in-addr.arpa"]["dnssec_keys"] == []


def test_a_name_with_no_reverse_zone_is_reported(snapshot):
    """netbox_dns skips a PTR with nowhere to go without a word"""
    typo = [z for z in snapshot.zones if z["name"] != "10.in-addr.arpa"]
    assert any("no reverse zone" in p for p in unzoned(loads(snapshot, zones=typo)))
