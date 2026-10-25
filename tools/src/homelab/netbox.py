"""Reading NetBox by natural key.

IDs are per-instance and the instance moves - laptop, hosted, restored - so nothing here
ever holds one. Objects are fetched in bulk and joined on prefix strings, vids, slugs and
names, which are the same in every copy of the data.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pynetbox

from . import secrets

ITEM_REF_ENV = "NETBOX_ITEM_REF"
DEFAULT_ITEM_REF = "op://Network/NetBox API Token - Homelab"


class Unreachable(Exception):
    """NetBox could not be read. Nothing downstream can proceed without it."""


@dataclass(frozen=True)
class Snapshot:
    sites: list[dict] = field(default_factory=list)
    vlans: list[dict] = field(default_factory=list)
    prefixes: list[dict] = field(default_factory=list)
    addresses: list[dict] = field(default_factory=list)
    asns: list[dict] = field(default_factory=list)
    zones: list[dict] = field(default_factory=list)
    dnssec_policies: list[dict] = field(default_factory=list)
    records: list[dict] = field(default_factory=list)
    devices: list[dict] = field(default_factory=list)
    services: list[dict] = field(default_factory=list)
    fhrp_groups: list[dict] = field(default_factory=list)
    bgp_sessions: list[dict] = field(default_factory=list)
    bgp_peer_groups: list[dict] = field(default_factory=list)
    routing_policy_rules: list[dict] = field(default_factory=list)
    virtual_machines: list[dict] = field(default_factory=list)


def connection(item: str | None = None) -> tuple[str, str]:
    item = (item or os.environ.get(ITEM_REF_ENV, DEFAULT_ITEM_REF)).rstrip("/")
    try:
        url, token = secrets.resolve(f"{item}/hostname", f"{item}/credential")
    except secrets.SecretError as exc:
        raise Unreachable(str(exc)) from exc
    return url, token


def api(item: str | None = None) -> pynetbox.api:
    url, token = connection(item)
    return pynetbox.api(url, token=token)


def fetch(item: str | None = None) -> Snapshot:
    nb = api(item)
    try:
        return Snapshot(
            sites=[dict(o) for o in nb.dcim.sites.all()],
            vlans=[dict(o) for o in nb.ipam.vlans.all()],
            prefixes=[dict(o) for o in nb.ipam.prefixes.all()],
            addresses=[dict(o) for o in nb.ipam.ip_addresses.all()],
            asns=[dict(o) for o in nb.ipam.asns.all()],
            zones=[dict(o) for o in nb.plugins.netbox_dns.zones.all()],
            dnssec_policies=[dict(o) for o in nb.plugins.netbox_dns.dnssecpolicies.all()],
            records=[dict(o) for o in nb.plugins.netbox_dns.records.all()],
            devices=[dict(o) for o in nb.dcim.devices.all()],
            services=[dict(o) for o in nb.ipam.services.all()],
            fhrp_groups=[dict(o) for o in nb.ipam.fhrp_groups.all()],
            bgp_sessions=[dict(o) for o in nb.plugins.bgp.session.all()],
            bgp_peer_groups=[dict(o) for o in nb.plugins.bgp.peer_group.all()],
            routing_policy_rules=[dict(o) for o in nb.plugins.bgp.routing_policy_rule.all()],
            virtual_machines=[dict(o) for o in nb.virtualization.virtual_machines.all()],
        )
    except Exception as exc:
        raise Unreachable(f"{nb.base_url}: {exc}") from exc
