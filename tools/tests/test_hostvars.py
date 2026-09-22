import dataclasses
import json

from conftest import record
from homelab.model import load
from homelab.render.hostvars import host_vars


def test_ansible_gets_plain_data(snapshot):
    """Ansible templates variables and never sees an ipaddress object as anything but
    an error - so everything handed over has to survive a round trip through JSON."""
    rendered = host_vars(load(snapshot), "ratatoskr")
    assert json.loads(json.dumps(rendered)) == rendered


def test_records_sharing_a_name_and_type_are_one_rrset(snapshot):
    """PowerDNS replaces a whole RRset per entry, so two entries for one name and type
    would leave only the second record."""
    both = [*snapshot.records, record("one.test", "dns.one.test.", "A", "10.0.200.9")]
    zones = host_vars(load(dataclasses.replace(snapshot, records=both)), "ratatoskr")
    (forward,) = [z for z in zones["netbox_zones"] if z["name"] == "one.test"]
    assert forward["rrsets"] == [
        {"name": "dns.one.test.", "type": "A", "ttl": 3600,
         "records": ["10.0.200.1", "10.0.200.9"]}
    ]
