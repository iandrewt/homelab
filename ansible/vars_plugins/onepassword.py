"""Secrets from 1Password, resolved through the SDK in Ansible's own process.

Every task runs in a worker that Ansible detaches from the terminal, so a lookup made from
a task asks for approval again each time. This resolves what an inventory's `onepassword.yml`
declares before any worker starts, on the one SDK client the NetBox plugin also uses.
A reference may name `{host}`, `{site}`, the host's slug in NetBox, and `{primary_site}`, the
zones' primary's.
"""

from pathlib import Path

import yaml
from ansible.errors import AnsibleError
from ansible.inventory.host import Host
from ansible.plugins.vars import BaseVarsPlugin

DOCUMENTATION = """
name: onepassword
short_description: Secrets from 1Password
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

_resolved: dict[str, str] = {}


class VarsModule(BaseVarsPlugin):
    REQUIRES_ENABLED = True

    def get_vars(self, loader, path, entities, cache=True):
        super().get_vars(loader, path, entities)
        from homelab import inventory, netbox, secrets

        declared = Path(path) / "onepassword.yml"
        if not declared.is_file():
            return {}
        declared = yaml.safe_load(declared.read_text()) or {}
        item = self.get_option("netbox_item")
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
                site = inventory.site_of(host.name, host.vars.get("sites"), item)
                primary = inventory.dns_primary_site(item)
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
