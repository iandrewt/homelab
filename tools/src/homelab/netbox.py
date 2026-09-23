"""Reading NetBox by natural key.

IDs are per-instance and the instance moves - laptop, hosted, restored - so nothing here
ever holds one. Objects are fetched in bulk and joined on prefix strings, vids, slugs and
names, which are the same in every copy of the data.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import secrets

ITEM_REF_ENV = "NETBOX_ITEM_REF"
DEFAULT_ITEM_REF = "op://Homelab/homelab NetBox API token"


class Unreachable(Exception):
    """NetBox could not be read. Nothing downstream can proceed without it."""


@dataclass(frozen=True)
class Snapshot:
    sites: list[dict] = field(default_factory=list)
    vlan_groups: list[dict] = field(default_factory=list)
    vlans: list[dict] = field(default_factory=list)
    prefixes: list[dict] = field(default_factory=list)
    addresses: list[dict] = field(default_factory=list)
    asns: list[dict] = field(default_factory=list)
    communities: list[dict] = field(default_factory=list)
    zones: list[dict] = field(default_factory=list)
    dnssec_policies: list[dict] = field(default_factory=list)
    records: list[dict] = field(default_factory=list)
    devices: list[dict] = field(default_factory=list)
    virtual_machines: list[dict] = field(default_factory=list)


def connection(item: str | None = None) -> tuple[str, str]:
    item = (item or os.environ.get(ITEM_REF_ENV, DEFAULT_ITEM_REF)).rstrip("/")
    try:
        url, token = secrets.resolve(f"{item}/hostname", f"{item}/credential")
    except secrets.SecretError as exc:
        raise Unreachable(str(exc)) from exc
    return url, token


def fetch(item: str | None = None) -> Snapshot:
    url, token = connection(item)

    try:
        import pynetbox
    except ModuleNotFoundError as exc:  # pragma: no cover - packaging failure
        raise Unreachable("pynetbox is not installed") from exc

    api = pynetbox.api(url, token=token)
    try:
        return Snapshot(
            sites=[dict(o) for o in api.dcim.sites.all()],
            vlan_groups=[dict(o) for o in api.ipam.vlan_groups.all()],
            vlans=[dict(o) for o in api.ipam.vlans.all()],
            prefixes=[dict(o) for o in api.ipam.prefixes.all()],
            addresses=[dict(o) for o in api.ipam.ip_addresses.all()],
            asns=[dict(o) for o in api.ipam.asns.all()],
            communities=[dict(o) for o in api.plugins.bgp.community.all()],
            zones=[dict(o) for o in api.plugins.netbox_dns.zones.all()],
            dnssec_policies=[dict(o) for o in api.plugins.netbox_dns.dnssecpolicies.all()],
            records=[dict(o) for o in api.plugins.netbox_dns.records.all()],
            devices=[dict(o) for o in api.dcim.devices.all()],
            virtual_machines=[dict(o) for o in api.virtualization.virtual_machines.all()],
        )
    except Exception as exc:
        raise Unreachable(f"{url}: {exc}") from exc
