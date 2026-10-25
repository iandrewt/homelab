from ansible.errors import AnsibleFilterError


def proxmox_interfaces(nics, site, primary, bond):
    from homelab.render.ifupdown2 import interfaces

    try:
        return interfaces(nics, site, primary, bond)
    except ValueError as bad:
        raise AnsibleFilterError(str(bad)) from None


def address_in(nics, prefix):
    """The node's active address inside `prefix`, without its length."""
    import ipaddress

    net = ipaddress.ip_network(prefix)
    found = [
        str(ipaddress.ip_interface(a["address"]).ip)
        for n in nics
        for a in n["ip_addresses"]
        if a["status"]["value"] == "active" and ipaddress.ip_interface(a["address"]).ip in net
    ]
    if len(found) != 1:
        raise AnsibleFilterError(f"expected one address in {prefix} on this node, found {found or 'none'}")
    return found[0]


class FilterModule:
    def filters(self):
        return {"proxmox_interfaces": proxmox_interfaces, "address_in": address_in}
