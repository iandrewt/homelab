import shutil
import subprocess

import pytest
import yaml
from conftest import DNS_ONE, K8S_1, OTHER, SITE
from homelab.model import load
from homelab.render import frr, seed


def site_of(snapshot, slug=SITE):
    return load(snapshot)["sites"][slug]


ROUTERS = {SITE: "udm", OTHER: "udm-two"}


def render(snapshot, slug=SITE) -> str:
    site = site_of(snapshot, slug)
    router = ROUTERS[slug]
    return frr.frr_config(site, router, {g["name"]: "secret" for g in site["routers"][router]["peer_groups"]})


def test_a_speaker_may_announce_only_what_it_originates_and_only_from_its_own_as(snapshot):
    """Every listen range is `remote-as external`, so acceptance is bound to the route:
    inside a speaker group's blocks or assigned addresses, and heard from its AS - with
    Cilium's node-internal ASNs behind it, which is why the match is the first AS, not
    the origin."""
    config = render(snapshot)
    assert f"ip prefix-list SPEAKER-{K8S_1}-V4 seq 5 permit 10.0.128.0/21 le 32" in config
    assert "permit 10.0.16.128/32" in config
    assert f"bgp as-path access-list SPEAKER-{K8S_1} permit ^{K8S_1}_" in config
    assert (f" match ip address prefix-list SPEAKER-{K8S_1}-V4\n match as-path SPEAKER-{K8S_1}"
            in config)


def test_every_clusters_nodes_share_one_listen_range(snapshot):
    """A summary prefix carries the tag, so a new cluster's node network needs nothing on the
    router: the group is named for the summary's role, having no VLAN of its own."""
    config = render(snapshot)
    assert " bgp listen range 10.0.8.0/24 peer-group KUBERNETES-NODES" in config
    assert "10.0.8.0/27" not in config


def test_each_site_accepts_the_recursor_from_its_own_fleet(snapshot):
    """One anycast address, n fleets: each router takes it from the fleet at its site."""
    for slug in (SITE, OTHER):
        config = render(snapshot, slug)
        assert "permit 10.239.0.53/32" in config


def test_ipv6_reaches_the_router_with_a_next_hop_it_can_install(snapshot):
    """GoBGP sends IPv6 routes over an IPv4 session with an IPv4-mapped next hop, which the
    UDM's kernel rejects. So a cluster has an IPv6 session that carries IPv6 alone, and a
    mapped next hop is denied before any speaker's permit - from either session.
    """
    config = render(snapshot)
    ipv4 = config.split(" address-family ipv4 unicast")[1].split(" exit-address-family")[0]
    ipv6 = config.split(" address-family ipv6 unicast")[1].split(" exit-address-family")[0]

    assert " bgp listen range 2001:db8:1:1080::/60 peer-group KUBERNETES-NODES-V6" in config
    assert "neighbor KUBERNETES-NODES-V6 activate" in ipv6
    assert "KUBERNETES-NODES-V6" not in ipv4
    for route_map in ("FROM-KUBERNETES-NODES", "FROM-KUBERNETES-NODES-V6"):
        assert f"route-map {route_map} deny 5\n match ipv6 next-hop prefix-list IPV4-MAPPED" in config


def test_a_range_accepts_only_the_speaker_groups_with_a_session_on_it(snapshot):
    """A dynamic peer's ASN is its own word, so a node claiming the DNS fleet's ASN from the
    node network still can't originate the recursor."""
    config = render(snapshot)
    kubernetes = config.split("route-map FROM-KUBERNETES-NODES ")[1:]
    assert kubernetes and all(f"SPEAKER-{DNS_ONE}" not in part.split("!")[0] for part in kubernetes)
    assert f"route-map FROM-SERVICES permit 10\n match ip address prefix-list SPEAKER-{DNS_ONE}-V4" in config


def ethernet(config: str) -> dict:
    return yaml.safe_load(config)["network"]["ethernets"]["primary"]


def test_both_families_override_alike(snapshot):
    """networkd shares one [DHCP] section between them, so netplan rejects the whole file
    when the two disagree - and a missing one counts as disagreeing."""
    site = site_of(snapshot)
    for resolver in (None, site["dns"]["servers"]["ratatoskr"]):
        host = ethernet(seed.network_config(site, resolver=resolver))
        assert host["dhcp4-overrides"] == host["dhcp6-overrides"]


def test_a_resolver_asks_its_gateway_never_the_vip(snapshot):
    """Nothing a resolver needs to build itself may come from the VIP it will serve, and
    the lease will hand out the VIP by DHCP and RA alike."""
    site = site_of(snapshot)
    resolver = ethernet(seed.network_config(site, resolver=site["dns"]["servers"]["ratatoskr"]))
    assert resolver["nameservers"]["addresses"] == ["10.0.30.1", "2001:db8:1:1300::1"]
    assert resolver["dhcp4-overrides"] == {"use-domains": True, "use-dns": False}
    assert resolver["ra-overrides"] == {"use-dns": False}
    unit = seed.network_unit(site, resolver=site["dns"]["servers"]["ratatoskr"])
    assert "DNS=10.0.30.1\nDNS=2001:db8:1:1300::1\n" in unit
    assert unit.count("UseDNS=no") == 3


@pytest.mark.skipif(shutil.which("vtysh") is None, reason="needs FRR's vtysh")
def test_frr_accepts_every_sites_config(snapshot, tmp_path):
    """It's pasted into the UDM by hand, and nothing else parses it before then."""
    for slug in (SITE, OTHER):
        path = tmp_path / f"{slug}-frr.conf"
        path.write_text(render(snapshot, slug))
        checked = subprocess.run(["vtysh", "--dryrun", "--inputfile", str(path)], capture_output=True, text=True,
                                 check=False)
        assert checked.returncode == 0, checked.stdout + checked.stderr
