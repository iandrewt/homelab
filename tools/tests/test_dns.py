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
    zones = dns._config(load(snap))["zones"]

    assert "apps.k8s-1.one.test." not in zones
    assert "*" not in zones
    assert "delegations" in zones["one.test."]["sources"]
    assert zones["k8s-1.one.test."]["sources"] == ["netbox", "delegations"]
