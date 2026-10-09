"""Site facts from NetBox, through the same model the `homelab` CLI reads.

`nb_inventory` says which hosts exist and what they are; this says what is true of the
site each one is at. NetBox is read once per run, with Ansible's own token.
"""

import os

from ansible.errors import AnsibleError
from ansible.inventory.host import Host
from ansible.plugins.vars import BaseVarsPlugin

ITEM_REF = "op://Homelab/Ansible NetBox API Token"


class VarsModule(BaseVarsPlugin):
    REQUIRES_ENABLED = True

    _model = None

    def get_vars(self, loader, path, entities, cache=True):
        super().get_vars(loader, path, entities)
        hosts = [e for e in entities if isinstance(e, Host)]
        if not hosts:
            return {}

        model = self._load()
        data = {}
        for host in hosts:
            # A host NetBox doesn't know is at no site, and gets nothing
            site = next((s for s in model["sites"].values() if host.name in s["hosts"]), None)
            if site is not None:
                data.update(
                    netbox_site=site,
                    netbox_sites=model["sites"],
                    netbox_zones=list(model["zones"].values()),
                )
        return data

    @classmethod
    def _load(cls):
        if cls._model is None:
            try:
                from homelab import model, netbox
            except ModuleNotFoundError as missing:
                raise AnsibleError(
                    f"{missing} - run Ansible through `uv run`, which provides homelab"
                ) from None

            try:
                cls._model = model.load(netbox.fetch(os.environ.get("NETBOX_ITEM_REF", ITEM_REF)))
            except netbox.Unreachable as bad:
                raise AnsibleError(f"NetBox: {bad}") from None
        return cls._model
