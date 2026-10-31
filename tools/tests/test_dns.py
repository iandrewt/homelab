import dataclasses

from conftest import soa, zone
from homelab import dns
from homelab.model import load


def test_octodns_never_writes_a_zone_its_owner_writes_over_rfc_2136(snapshot):
    """octoDNS deletes whatever NetBox doesn't hold, which in such a zone is every record;
    and the zone holding its delegation publishes its DS."""
    snap = dataclasses.replace(
        snapshot,
        zones=[*snapshot.zones, zone("k8s-1.one.test"), zone("apps.k8s-1.one.test", tags=["rfc2136"])],
        records=[*snapshot.records, soa("k8s-1.one.test"), soa("apps.k8s-1.one.test")],
    )
    zones = dns._config(load(snap), dns.Api("one", "10.0.40.1", 8081, "/dev/null"))["zones"]

    assert "apps.k8s-1.one.test." not in zones
    assert "*" not in zones
    assert "delegations" in zones["one.test."]["sources"]
    assert zones["k8s-1.one.test."]["sources"] == ["netbox", "delegations"]


def test_a_public_reverse_zone_goes_to_route53_by_its_name_in_netbox(snapshot):
    """Its name holds a site's prefix, which the repo never does, so the config can't list it."""
    reverse = "1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa"
    snap = dataclasses.replace(snapshot, zones=[*snapshot.zones, zone(reverse, view="public")])
    zones = dns._config(load(snap), dns.Api("one", "10.0.40.1", 8081, "/dev/null"))["zones"]

    assert zones[f"{reverse}."]["targets"] == ["route53"]
    assert "*.arpa." not in zones
