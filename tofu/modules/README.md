# modules

**`vm`** - a Proxmox VM via cloud-init. Wired in for `hugin`.

Needs the 1Password service account credential (`OP_SERVICE_ACCOUNT_TOKEN`) and a real
Proxmox endpoint to actually apply - structurally validated (`tofu validate`) against
the cached provider schema, not yet applied against real hardware.
