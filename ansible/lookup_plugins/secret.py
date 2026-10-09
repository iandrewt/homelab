"""An op:// reference, through the SDK - for the inventory, which Ansible templates in its
own process. Anything a task needs belongs in inventory/onepassword.yml: a task runs in a
worker, where every call would ask for approval again."""

from ansible.errors import AnsibleError
from ansible.plugins.lookup import LookupBase


class LookupModule(LookupBase):
    def run(self, terms, variables=None, **kwargs):
        from homelab import secrets

        try:
            return secrets.resolve(*terms)
        except secrets.SecretError as bad:
            raise AnsibleError(str(bad)) from None
