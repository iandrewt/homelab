"""The model as Ansible variables, for `ansible/vars_plugins/netbox.py`."""


def host_vars(ipam: dict, hostname: str) -> dict:
    """What a host needs to know about where it is. A host NetBox doesn't know is at no
    site, and gets nothing."""
    site = next((s for s in ipam["sites"].values() if hostname in s["hosts"]), None)
    if site is None:
        return {}
    return {
        "netbox_site": site,
        "netbox_sites": ipam["sites"],
        "netbox_zones": list(ipam["zones"].values()),
    }
