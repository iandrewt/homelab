"""What Ansible's plugins share: NetBox read once per process, and each host's site."""

import functools

from . import netbox
from .model import load


@functools.cache
def model(item: str | None = None) -> dict:
    return load(netbox.fetch(item))


def site_of(host: str, item: str | None = None) -> dict | None:
    """A host NetBox doesn't know is at no site."""
    return next((s for s in model(item)["sites"].values() if host in s["hosts"]), None)


def dns_primary_site(item: str | None = None) -> dict | None:
    return next(
        (s for s in model(item)["sites"].values() if any(v["primary"] for v in s["dns"]["servers"].values())),
        None,
    )
