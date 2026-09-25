"""A Proxmox node's /etc/network/interfaces, from its interfaces in NetBox.

SDN owns `<bridge>.<vid>` for every VNet, so a node joins a VNet with an address on the
VNet's own bridge, and any other VLAN with a subinterface.
"""

import ipaddress

HEADER = "# Written by Ansible (proxmox_node) from NetBox. Changes here are overwritten.\n"


def interfaces(nics: list[dict], site: dict, primary: list[str], bond: dict) -> str:
    """`nics` are the node's interfaces as `nb_inventory` gives them, `primary` its primary
    addresses - the interface holding one gets the default route."""
    nics = sorted((n for n in nics if n["enabled"] and not n["mgmt_only"]), key=lambda n: n["name"])
    vnets = {v["vid"]: v["vnet"] for v in site["vlans"].values() if v["vnet"]}
    kind = {n["name"]: _kind(n, vnets) for n in nics}

    def members(key: str, name: str) -> list[str]:
        return [n["name"] for n in nics if _ref(n, key) == name]

    def children(name: str) -> list[dict]:
        return [n for n in nics if _ref(n, "parent") == name and kind[n["name"]] == "vlan"]

    stanzas = ["auto lo\niface lo inet loopback"]
    for group in ("physical", "lag", "bridge", "vlan", "vnet"):
        for nic in (n for n in nics if kind[n["name"]] == group):
            name = nic["name"]
            attrs: list[tuple[str, object]] = []
            if group == "lag":
                attrs += [("bond-slaves", " ".join(members("lag", name)))]
                attrs += [(f"bond-{k}", v) for k, v in bond.items()]
            elif group == "bridge":
                attrs += [("bridge-ports", " ".join(members("bridge", name)) or "none"),
                          ("bridge-stp", "off")]
                if _mode(nic) == "tagged":
                    vids = set(vnets) | {_vid(c) for c in children(name)}
                    vids |= {_vid(p) for p in nics if _ref(p, "bridge") == name and _vid(p)}
                    attrs += [("bridge-vlan-aware", "yes"),
                              ("bridge-vids", " ".join(map(str, sorted(vids))))]
            elif group == "vlan" and name != f"{_ref(nic, 'parent')}.{_vid(nic)}":
                attrs += [("vlan-raw-device", _ref(nic, "parent")), ("vlan-id", _vid(nic))]
            if _ref(nic, "bridge") and _vid(nic):
                attrs.append(("bridge-pvid", _vid(nic)))
            # ifupdown2 resets a bridge with no MTU to 1500, whatever its ports say; a
            # VNet's comes from the SDN zone
            if group != "vnet":
                attrs.append(("mtu", nic["mtu"]))

            used = (group != "physical" or _ref(nic, "lag") or _ref(nic, "bridge")
                    or nic["ip_addresses"] or children(name))
            # SDN's own stanza brings a VNet's bridge up, once there is one
            auto = bool(used) and group != "vnet"
            stanzas.append(_stanza(nic, attrs, auto, site, primary))

    return HEADER + "\n" + "\n\n".join(stanzas) + "\n\nsource /etc/network/interfaces.d/*\n"


def _kind(nic: dict, vnets: dict) -> str:
    kind = nic["type"]["value"]
    if kind in ("lag", "bridge"):
        return kind
    if kind != "virtual":
        return "physical"
    vnet = vnets.get(_vid(nic))
    if vnet is None:
        return "vlan"
    if nic["name"] != vnet:
        raise ValueError(
            f"{nic['name']} is on VLAN {_vid(nic)}, which is VNet {vnet!r} - SDN owns its "
            f"subinterface, so name the interface {vnet!r} to join the VNet's bridge"
        )
    return "vnet"


def _stanza(nic: dict, attrs: list, auto: bool, site: dict, primary: list[str]) -> str:
    name = nic["name"]
    families = {4: [], 6: []}
    for address in nic["ip_addresses"]:
        if address["status"]["value"] == "active":
            families[address["family"]["value"]].append(address["address"])

    lines = [f"auto {name}"] if auto else []
    lines += [f"iface {name} inet {'static' if families[4] else 'manual'}"]
    lines += _addresses(families[4], 4, site, primary)
    lines += [f"\t{k} {v}" for k, v in attrs if v is not None]
    if nic["description"]:
        lines.append(f"#{nic['description']}")
    if families[6]:
        lines += ["", f"iface {name} inet6 static"]
        lines += _addresses(families[6], 6, site, primary)
        lines.append("\tipv6-addrgen on")
    return "\n".join(lines)


def _addresses(addresses: list[str], family: int, site: dict, primary: list[str]) -> list[str]:
    lines = [f"\taddress {a}" for a in addresses]
    for address in addresses:
        ip = ipaddress.ip_interface(address).ip
        if str(ip) in primary:
            lines.append(f"\tgateway {_gateway(ip, family, site)}")
    return lines


def _gateway(ip, family: int, site: dict) -> str:
    """NetBox doesn't scope an address to a VLAN, so its VLAN is the one whose prefix holds it."""
    for vlan in site["vlans"].values():
        prefix = vlan[f"ipv{family}"]
        if prefix and ip in ipaddress.ip_network(prefix):
            return vlan[f"gateway_ipv{family}"]
    raise ValueError(f"{ip} is in no VLAN's prefix at site {site['slug']!r}")


def _ref(nic: dict, key: str) -> str | None:
    return (nic.get(key) or {}).get("name")


def _vid(nic: dict) -> int | None:
    return (nic.get("untagged_vlan") or {}).get("vid")


def _mode(nic: dict) -> str | None:
    return (nic.get("mode") or {}).get("value")
