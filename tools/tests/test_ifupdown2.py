import pytest
from homelab.render.ifupdown2 import interfaces

SITE = {
    "slug": "one",
    "vlans": {
        "Management": {"vid": 10, "vnet": None, "ipv4": "10.0.10.0/24", "ipv6": None,
                       "gateway_ipv4": "10.0.10.1", "gateway_ipv6": None},
        "Storage": {"vid": 210, "vnet": "storage", "ipv4": "10.0.210.0/24", "ipv6": None,
                    "gateway_ipv4": "10.0.210.1", "gateway_ipv6": None},
    },
}


def nic(name, kind="virtual", *, vid=None, parent=None, lag=None, bridge=None, mode=None,
        addresses=()):
    return {
        "name": name, "type": {"value": kind}, "enabled": True, "mgmt_only": False,
        "mtu": None, "description": "", "mode": mode and {"value": mode},
        "untagged_vlan": vid and {"vid": vid}, "parent": parent and {"name": parent},
        "lag": lag and {"name": lag}, "bridge": bridge and {"name": bridge},
        "ip_addresses": [{"address": a, "family": {"value": 4}, "status": {"value": "active"}}
                         for a in addresses],
    }


def trunk(*extra):
    return [nic("eno1", "10gbase-sr", bridge="vmbr0"),
            nic("vmbr0", "bridge", mode="tagged"), *extra]


def test_a_node_joining_a_vnet_by_subinterface_is_refused():
    """SDN makes `vmbr0.210` a port of the VNet's bridge, and an address on a bridge port
    is dropped without a word - which is how yggdrasil lost its storage address."""
    with pytest.raises(ValueError, match="name the interface 'storage'"):
        interfaces(trunk(nic("vmbr0.210", vid=210, parent="vmbr0", addresses=["10.0.210.5/24"])),
                   SITE, [], {})
