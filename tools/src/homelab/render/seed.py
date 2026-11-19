"""Seed documents: cloud-init's, for hosts installed from media, and networkd's, for an image that reads
credentials."""

import uuid

import yaml


class IndentedDumper(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def _document(payload: dict) -> str:
    return yaml.dump(payload, Dumper=IndentedDumper, sort_keys=False)


def meta_data(hostname: str, region: str | None, *, ssh_keys=(), instance_id: str | None = None) -> str:
    return _document({
        "instance-id": instance_id or uuid.uuid4().hex,
        "local-hostname": hostname,
        "cloud-name": "homelab",
        **({"region": region} if region else {}),
        "public-keys": list(ssh_keys),
    })


def cloud_config(payload: dict) -> str:
    # cloud-init types a document by its first line, so nothing may precede the marker.
    return "#cloud-config\n" + _document(payload)


def network_config(site, *, resolver=None) -> str:
    ethernet = {"match": {"name": "e*"}, "dhcp4": True, "dhcp6": True, "emit-lldp": True}

    overrides = {"use-domains": True}
    if resolver:
        ethernet["nameservers"] = {"addresses": _gateways(site, resolver)}
        overrides["use-dns"] = False
        ethernet["ra-overrides"] = {"use-dns": False}
    ethernet["dhcp4-overrides"] = dict(overrides)
    ethernet["dhcp6-overrides"] = dict(overrides)

    return _document({"network": {"version": 2, "ethernets": {"primary": ethernet}}})


def _gateways(site, resolver) -> list[str]:
    vlan = site["vlans"][resolver["vlan"]]
    return [g for g in (vlan["gateway_ipv4"], vlan["gateway_ipv6"]) if g]


def network_unit(site, *, resolver=None) -> str:
    """The same network as networkd's own .network, for an image that reads no network-config."""
    unit = "[Match]\nType=ether\nKind=!*\n\n[Network]\nDHCP=yes\nEmitLLDP=yes\nLinkLocalAddressing=ipv6\nUseDomains=yes\n"
    if resolver:
        unit += "".join(f"DNS={g}\n" for g in _gateways(site, resolver))
        unit += "\n[DHCPv4]\nUseDNS=no\n\n[DHCPv6]\nUseDNS=no\n\n[IPv6AcceptRA]\nUseDNS=no\n"
    return unit
