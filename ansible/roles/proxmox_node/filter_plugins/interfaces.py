from ansible.errors import AnsibleFilterError


def proxmox_interfaces(nics, site, primary, bond):
    from homelab.render.ifupdown2 import interfaces

    try:
        return interfaces(nics, site, primary, bond)
    except ValueError as bad:
        raise AnsibleFilterError(str(bad)) from None


class FilterModule:
    def filters(self):
        return {"proxmox_interfaces": proxmox_interfaces}
