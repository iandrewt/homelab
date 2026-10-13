"""A Proxmox host's answer file, from NetBox and the cluster file that names the host."""

import ipaddress

import tomli_w
import yaml

from .. import netbox, secrets
from ..model import load
from ..repo import Repo
from .shell import BootstrapError, run

BOOT_TAG = "proxmox-boot"


def render(host: str) -> str:
    found = facts(netbox.api(), host)
    clusters = [yaml.safe_load(p.read_text()) for p in Repo.discover().clusters.glob("*.yaml")]
    cluster = next((c for c in clusters if host in c["nodes"]), None)
    if cluster is None:
        raise BootstrapError(f"{host} is a node in no cluster file")
    vault = cluster["onepassword_vault"]
    password, ssh_key = secrets.resolve(
        f"op://{cluster['onepassword_break_glass_vault']}/Proxmox Root Login - {host}/password",
        f"op://{vault}/SSH Key - {vault}/public key",
    )
    root_hash = run("openssl", "passwd", "-6", "-stdin", input=password, capture=True).strip()
    site = load()["sites"][found["site"]]
    ip = ipaddress.ip_interface(found["cidr"]).ip
    gateway = next(
        v["gateway_ipv6"] for v in site["vlans"].values()
        if v["ipv6"] and ip in ipaddress.ip_network(v["ipv6"])
    )
    return tomli_w.dumps(
        answer(found, cluster, root_hash, ssh_key, gateway, site["anycast"]["ipv6"])
    )


def answer(
    facts: dict, cluster: dict, root_hash: str, ssh_key: str, gateway: str, resolver: str
) -> dict:
    return {
        "global": {
            "keyboard": "en-us",
            "country": "au",
            "fqdn": facts["fqdn"],
            "mailto": cluster["mailto"],
            "timezone": cluster["timezone"],
            "root-password-hashed": root_hash,
            "root-ssh-keys": [ssh_key],
        },
        "network": {
            "source": "from-answer",
            "cidr": facts["cidr"],
            "gateway": gateway,
            "dns": resolver,
            "filter": {"ID_NET_NAME_MAC": "enx" + facts["mac"].replace(":", "").lower()},
        },
        # The boot disk holds only the OS; guests live on the node's ZFS pools
        "disk-setup": {
            "filesystem": "ext4",
            "lvm": {"maxvz": 0},
            # This disk gets wiped, so it has to be the one by both names
            "filter-match": "all",
            "filter": {"ID_WWN": facts["wwn"], "ID_SERIAL_SHORT": facts["serial"]},
        },
    }


def facts(api, host: str) -> dict:
    """Where the host's primary address lives, the NIC behind it, and the disk tagged for
    the OS."""
    device = api.dcim.devices.get(name=host)
    if device is None:
        raise BootstrapError(f"{host} isn't a device in NetBox")
    address = api.ipam.ip_addresses.get(address=device.primary_ip6.address, device=host)
    holder = address.assigned_object.name
    uplink = next(
        i for i in api.dcim.interfaces.filter(device=host)
        if i.primary_mac_address and (i.name == holder or (i.bridge and i.bridge.name == holder))
    )
    (disk,) = api.dcim.modules.filter(device=host, tag=BOOT_TAG)
    return {
        "site": device.site.slug,
        "fqdn": address.dns_name,
        "cidr": address.address,
        "mac": str(uplink.primary_mac_address),
        "wwn": disk.asset_tag.removeprefix("wwn-"),
        "serial": disk.serial,
    }
