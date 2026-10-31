"""Secrets from 1Password, resolved through the SDK in Ansible's own process.

Every task runs in a worker that Ansible detaches from the terminal, so a lookup made from
a task asks for approval again each time. This resolves what `inventory/onepassword.yml`
declares before any worker starts, on the one SDK client the NetBox plugin also uses.
A reference may name `{host}`, `{site}`, the host's slug in NetBox, and `{primary_site}`, the
zones' primary's.
"""

import os
from pathlib import Path

import yaml
from ansible.errors import AnsibleError
from ansible.inventory.host import Host
from ansible.plugins.vars import BaseVarsPlugin

DECLARED = Path(__file__).parent.parent / "inventory" / "onepassword.yml"
NETBOX_ITEM_REF = os.environ.get("NETBOX_ITEM_REF", "op://Network/NetBox API Token - Ansible")

_resolved: dict[str, str] = {}


class VarsModule(BaseVarsPlugin):
    REQUIRES_ENABLED = True

    def get_vars(self, loader, path, entities, cache=True):
        super().get_vars(loader, path, entities)
        from homelab import inventory, netbox, secrets

        declared = yaml.safe_load(DECLARED.read_text()) or {}
        data = {}
        for host in (e for e in entities if isinstance(e, Host)):
            groups = {g.name for g in host.get_groups()}
            wanted = {
                name: reference
                for group, references in declared.items() if group in groups
                for name, reference in references.items()
            }
            if not wanted:
                continue
            try:
                site = inventory.site_of(host.name, host.vars.get("sites"), NETBOX_ITEM_REF)
                primary = inventory.dns_primary_site(NETBOX_ITEM_REF)
                references = {
                    name: reference.format(host=host.name, site=site and site["slug"],
                                           primary_site=primary and primary["slug"])
                    for name, reference in wanted.items()
                }
                missing = sorted(set(references.values()) - _resolved.keys())
                if missing:
                    _resolved.update(zip(missing, secrets.resolve(*missing)))
            except (netbox.Unreachable, secrets.SecretError) as bad:
                raise AnsibleError(f"{host.name}: {bad}") from None
            data.update({name: _resolved[ref] for name, ref in references.items()})
        return data
