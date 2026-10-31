"""Site facts from NetBox, through the same model the `homelab` CLI reads.

`nb_inventory` says which hosts exist and what they are; this says what is true of the
site each one is at. NetBox is read once per run, with Ansible's own token.
"""

import os

from ansible.errors import AnsibleError
from ansible.inventory.host import Host
from ansible.plugins.vars import BaseVarsPlugin

ITEM_REF = os.environ.get("NETBOX_ITEM_REF", "op://Network/NetBox API Token - Ansible")


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

        data = {}
        for host in (e for e in entities if isinstance(e, Host)):
            try:
                model = inventory.model(ITEM_REF)
                site = inventory.site_of(host.name, host.vars.get("sites"), ITEM_REF)
            except netbox.Unreachable as bad:
                raise AnsibleError(f"NetBox: {bad}") from None
            if site is not None:
                data.update(
                    netbox_site=site,
                    netbox_sites=model["sites"],
                    netbox_zones=list(model["zones"].values()),
                )
        return data
