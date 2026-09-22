"""Site facts from NetBox, through the same model `homelab validate` checks.

`nb_inventory` says which hosts exist and what they are; this says what is true of the
site each one is at. NetBox is read once per run, with Ansible's own token, and a host
only ever sees data that validated.
"""

import os

from ansible.errors import AnsibleError
from ansible.inventory.host import Host
from ansible.plugins.vars import BaseVarsPlugin

ITEM_REF = "op://Homelab/Ansible NetBox API Token"


class VarsModule(BaseVarsPlugin):
    REQUIRES_ENABLED = True

    _ipam = None

    def get_vars(self, loader, path, entities, cache=True):
        super().get_vars(loader, path, entities)
        hosts = [e for e in entities if isinstance(e, Host)]
        if not hosts:
            return {}

        from homelab.render.hostvars import host_vars

        ipam = self._load()
        data = {}
        for host in hosts:
            data.update(host_vars(ipam, host.name))
        return data

    @classmethod
    def _load(cls):
        if cls._ipam is None:
            try:
                from homelab import model, netbox
            except ModuleNotFoundError as missing:
                raise AnsibleError(
                    f"{missing} - run Ansible through `uv run`, which provides homelab"
                ) from None

            try:
                snapshot = netbox.fetch(os.environ.get("NETBOX_ITEM_REF", ITEM_REF))
                cls._ipam = model.load(snapshot)
            except (netbox.Unreachable, model.InvalidSource) as bad:
                raise AnsibleError(f"NetBox: {bad}") from None
        return cls._ipam
