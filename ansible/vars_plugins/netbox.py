"""Site facts from NetBox, through the same model the `homelab` CLI reads.

`nb_inventory` says which hosts exist and what they are; this says what is true of the
site each one is at. NetBox is read once per run, with the tree's own token. It also writes the
host keys NetBox pins where ansible.cfg has ssh look for them, by each host's primary addresses.
"""

import functools
from pathlib import Path

from ansible.errors import AnsibleError
from ansible.inventory.host import Host
from ansible.plugins.vars import BaseVarsPlugin
from ansible.utils.display import Display

DOCUMENTATION = """
name: netbox
short_description: Site facts from NetBox
options:
  netbox_item:
    description: The API Credential this tree reads NetBox with.
    ini:
      - section: homelab
        key: netbox_item
    env:
      - name: NETBOX_ITEM_REF
    required: true
"""

KNOWN_HOSTS = Path(__file__).parent.parent / ".ansible" / "known_hosts"

_unpinned: set[str] = set()


class VarsModule(BaseVarsPlugin):
    REQUIRES_ENABLED = True

    def get_vars(self, loader, path, entities, cache=True):
        super().get_vars(loader, path, entities)
        try:
            from homelab import inventory, netbox
        except ModuleNotFoundError as missing:
            raise AnsibleError(
                f"{missing} - run Ansible through `uv run`, which provides homelab"
            ) from None

        item = self.get_option("netbox_item")
        data = {}
        for host in (e for e in entities if isinstance(e, Host)):
            try:
                model = inventory.model(item)
                site = inventory.site_of(host.name, host.vars.get("sites"), item)
            except netbox.Unreachable as bad:
                raise AnsibleError(f"NetBox: {bad}") from None
            _write_known_hosts(item)
            if site is not None:
                if host.name not in site["host_keys"] and host.name not in _unpinned:
                    Display().warning(f"{host.name} has no pinned host key, so ssh will refuse it: "
                                      f"`homelab pin ssh {host.name}`")
                    _unpinned.add(host.name)
                data.update(
                    netbox_site=site,
                    netbox_sites=model["sites"],
                    netbox_zones=list(model["zones"].values()),
                )
        return data


@functools.cache
def _write_known_hosts(item: str) -> None:
    from homelab import inventory

    model = inventory.model(item)
    lines = sorted(f"{','.join(pin['addresses'])} {pin['key']}\n"
                   for site in model["sites"].values() for pin in site["host_keys"].values() if pin["addresses"])
    KNOWN_HOSTS.parent.mkdir(exist_ok=True)
    KNOWN_HOSTS.write_text("".join(lines))
