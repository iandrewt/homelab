"""Seed documents: cloud-init's, for hosts installed from media."""

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
        vlan = site["vlans"][resolver["vlan"]]
        ethernet["nameservers"] = {
            "addresses": [g for g in (vlan["gateway_ipv4"], vlan["gateway_ipv6"]) if g]
        }
        overrides["use-dns"] = False
        ethernet["ra-overrides"] = {"use-dns": False}
    ethernet["dhcp4-overrides"] = dict(overrides)
    ethernet["dhcp6-overrides"] = dict(overrides)

    return _document({"network": {"version": 2, "ethernets": {"primary": ethernet}}})
