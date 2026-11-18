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


def lldp_trunk(nics):
    """Where UniFi learns the node's guests: the tagged bridge's port, and of a bond, the member on
    its switch's lowest port. UniFi places a neighbour on the member it heard it on, but learns the
    guests' MACs on the LAG's lowest port."""
    import re

    def ref(nic, key):
        return (nic.get(key) or {}).get("name")

    bridges = [n for n in nics if n["type"]["value"] == "bridge" and (n.get("mode") or {}).get("value") == "tagged"]
    if len(bridges) != 1:
        raise AnsibleFilterError(f"expected one tagged bridge on this node, found {[n['name'] for n in bridges]}")
    port = [n for n in nics if ref(n, "bridge") == bridges[0]["name"]]
    if len(port) != 1:
        raise AnsibleFilterError(f"expected one port on {bridges[0]['name']}, found {[n['name'] for n in port]}")
    trunk = port[0]
    if trunk["type"]["value"] != "lag":
        return {"chassis": trunk["name"], "send": trunk["name"]}

    def switch_port(nic):
        peers = nic.get("link_peers") or []
        number = re.search(r"(\d+)$", peers[0]["name"]) if peers else None
        if not number:
            raise AnsibleFilterError(f"{nic['name']} has no cable to a numbered switch port in NetBox")
        return int(number.group(1))

    members = [n for n in nics if ref(n, "lag") == trunk["name"]]
    return {"chassis": trunk["name"], "send": min(members, key=switch_port)["name"]}


class FilterModule:
    def filters(self):
        return {"proxmox_interfaces": proxmox_interfaces, "address_in": address_in, "lldp_trunk": lldp_trunk}
