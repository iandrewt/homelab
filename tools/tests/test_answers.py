import tomllib

import tomli_w
from homelab.bootstrap.answers import answer

FACTS = {
    "fqdn": "hugin.one.test", "cidr": "10.0.10.5/25",
    "mac": "02:00:00:00:00:01", "wwn": "0x5000000000000001", "serial": "TESTSERIAL0001",
}


def test_the_installer_is_told_the_disk_to_wipe_by_both_its_names():
    """Proxmox's filters match udev properties, and `all` means a disk has to carry both -
    so a stale WWN or serial in NetBox stops the install instead of choosing a disk."""
    toml = tomli_w.dumps(answer(FACTS, {"mailto": "m@one.test", "timezone": "UTC"}, "h", "k", "10.0.200.1"))
    disks = tomllib.loads(toml)["disk-setup"]
    assert disks["filter-match"] == "all"
    assert disks["filter"] == {"ID_WWN": "0x5000000000000001", "ID_SERIAL_SHORT": "TESTSERIAL0001"}


def test_the_install_nic_is_the_one_netbox_names_by_mac():
    net = answer(FACTS, {"mailto": "m@one.test", "timezone": "UTC"}, "h", "k", "10.0.200.1")["network"]
    assert net["filter"] == {"ID_NET_NAME_MAC": "enx020000000001"}
    assert net["gateway"] == "10.0.10.1"
