"""Secrets from 1Password, resolved through the SDK in Ansible's own process.

Every task runs in a worker that Ansible detaches from the terminal, so a lookup made from
a task asks for approval again each time. This resolves what an inventory's `onepassword.yml`
declares before any worker starts, on the one SDK client the NetBox plugin also uses.
A reference may name `{host}`, `{site}`, the host's slug in NetBox, and `{primary_site}`, the
zones' primary's. It also writes the public half of the tree's SSH key where every host's ssh
looks for it, which with `IdentitiesOnly` is the one key the agent is asked for.
"""

import re
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
  ssh_key:
    description: The SSH Key item this tree's hosts trust.
    ini:
      - section: homelab
        key: ssh_key
    required: true
"""

KEYS = Path(__file__).parent.parent / ".ansible"

_resolved: dict[str, str] = {}


class VarsModule(BaseVarsPlugin):
    REQUIRES_ENABLED = True

    def get_vars(self, loader, path, entities, cache=True):
        super().get_vars(loader, path, entities)
        from homelab import inventory, netbox, secrets

        hosts = [e for e in entities if isinstance(e, Host)]
        if not hosts:
            return {}
        try:
            data = {"ansible_ssh_private_key_file": _public_key(self.get_option("ssh_key"))}
        except secrets.SecretError as bad:
            raise AnsibleError(str(bad)) from None
        declared = Path(path) / "onepassword.yml"
        if not declared.is_file():
            return data
        declared = yaml.safe_load(declared.read_text()) or {}
        item = self.get_option("netbox_item")
        for host in hosts:
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


def _public_key(item: str) -> str:
    """Where the item's public key is, written there the first time it's asked for."""
    from homelab import secrets

    reference = f"{item.rstrip('/')}/public key"
    path = KEYS / f"{re.sub(r'[^A-Za-z0-9]+', '-', item.removeprefix('op://')).strip('-')}.pub"
    if reference not in _resolved:
        (_resolved[reference],) = secrets.resolve(reference)
        KEYS.mkdir(exist_ok=True)
        path.write_text(_resolved[reference].strip() + "\n")
    return str(path)
