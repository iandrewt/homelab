import json

from homelab.model import load
from homelab.render.hostvars import host_vars


def test_ansible_gets_plain_data(snapshot):
    """Ansible templates variables and never sees an ipaddress object as anything but
    an error - so everything handed over has to survive a round trip through JSON."""
    rendered = host_vars(load(snapshot), "ratatoskr")
    assert json.loads(json.dumps(rendered)) == rendered
