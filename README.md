# homelab

Infrastructure-as-code for my home network and home lab (site `alfheim`): addressing, host configuration, and the Proxmox/OpenTofu/Ansible layers that build and manage it.

**Status: pre-build.** Nothing physical exists yet - a NAS migration is still in progress, the hypervisor host isn't racked, and Proxmox isn't installed anywhere real.
Everything under `tofu/`, `ansible/`, `talos/`, `clusters/` is written and validated as far as it can be without real hardware, not yet applied against it.

## Layout

```
ipam/         Source of truth for addressing (ipam.yaml) + the generator/validator
ansible/      Host configuration - roles, inventory (generated), playbooks
tofu/         OpenTofu modules + the alfheim environment
docs/         Physical bootstrap procedures live under docs/bootstrap/
unifi/        Config for things UniFi owns that aren't Ansible/OpenTofu targets
```

## Using this repo

Regenerate IPAM outputs after editing `ipam/ipam.yaml` - never hand-edit
`ansible/inventory/hosts.yml`, it's generated:

```
python3 ipam/validate.py && python3 ipam/generate.py
```

Ansible targets are grouped by role and by OS (see `ipam.yaml`'s `hosts:` section):

```
ansible-galaxy collection install -r ansible/requirements.yml
cd ansible && ansible-playbook site.yml --limit <hostname>
cd ansible && ansible-playbook full-upgrade.yml
```

OpenTofu, from `tofu/envs/alfheim/`:

```
tofu init
tofu validate
tofu plan
tofu apply
```

## Status / TODO

**Credentials** (blocks `tofu apply` and any real Ansible run)

- [ ] Decide the 1Password vault, pass it as `onepassword_vault` when running `tofu`
- [ ] Create the 1Password items the code already expects: `yggdrasil Proxmox API token` (API Credential), `homelab control node SSH key` (SSH Key), `hugin PowerDNS API key` (API Credential)
- [ ] Set `OP_SERVICE_ACCOUNT_TOKEN` wherever `tofu`/`ansible` actually run from

**OpenTofu**

- [ ] Decide the state backend (R2 vs S3) - local-only right now
- [ ] Put the state/plan encryption passphrase in 1Password
- [ ] First real `tofu apply` - creates `hugin`, the primary DNS resolver VM - once `yggdrasil` (the hypervisor) exists and the above credentials are real

**Ratatoskr (Raspberry Pi)**

- [ ] Flash the SD card for real - not hands-on verified against real Pi 3B hardware
- [ ] Add its DHCP reservation once it's on the network

**BGP / DNS**

- [ ] Write and upload the UDM Pro's own FRR config (already drafted)
- [ ] Confirm real ECMP behaviour against the actual UDM Pro firmware in practice

**SOPS**

- [ ] Generate the bootstrap age key and escrow it somewhere durable, not this repo
      (only needed once cluster work starts)

**Known gaps, not yet built**

- [ ] Reboot-required alerting - needs a monitoring stack
- [ ] CI: static checks on a hosted runner, a self-hosted runner for anything touching
      the LAN
- [ ] Everything cluster/Talos-related
- [ ] Network config for the NAS hosts - addressed in `ipam.yaml`, not yet applied
