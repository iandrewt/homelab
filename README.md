# homelab

Infrastructure-as-code for my home network and home lab (site `alfheim`): addressing, host configuration, and the Proxmox/OpenTofu/Ansible layers that build and manage it.

## Layout

```
ipam/         Source of truth for addressing - ipam.yaml and its schema
ansible/      Host configuration - roles, inventory (generated), playbooks
tofu/         OpenTofu - modules, one file per cluster, one directory per layer
bootstrap/    Physical build: installer answer templates, ISO baking, runbooks
unifi/        Config for things UniFi owns that aren't Ansible/OpenTofu targets
tools/        Python: validates the hand-authored files, generates the derived ones
```

```
tofu/clusters/
  alfheim.yaml            cluster settings read by admin layers
  generated/alfheim.json  addressing, from ipam.yaml
  schema.json
tofu/admin/
  root/                   root@pam only - the tofu@pve user, ACME
  access/                 roles, users, pools, realms, every other token
  cluster/                cluster options, node config, storage, mappings
  images/                 cloud image downloads
tofu/workloads/
  alfheim/platform/       general VMs and containers
```

The tofu is shaped in a multi-cluster way to make it easier for others to re-use. To use it, just replace `alfheim.yaml` with your own cluster info.

Workloads are managed separately to the cluster itself. The `workloads` map in the cluster YAML is how you can onboard a new workload group. Each one gets a pool, a PVE user of its own, and a token written to 1Password. Anything running *inside* a workload that needs the Proxmox API itself is an `integration` under that group, and gets its own user, token and roles.

## Using this repo

`tools/` validates every hand-authored source file and generates everything derived from them.
Never hand-edit `ansible/inventory/hosts.yml` or `tofu/clusters/generated/`:

```
pip install -r requirements.txt
python3 tools/validate.py && python3 tools/generate.py
```

Ansible targets are grouped by role and by OS (see `ipam.yaml`'s `hosts:` section):

```
ansible-galaxy collection install -r ansible/requirements.yml
cd ansible && ansible-playbook site.yml --limit <hostname>
cd ansible && ansible-playbook full-upgrade.yml
```

OpenTofu, from a state directory under `tofu/admin/` or `tofu/workloads/`. Both variables
come from 1Password, and the passphrase has to be in the environment - state encryption is
resolved during `init`, before any provider runs:

```
export OP_SERVICE_ACCOUNT_TOKEN=...
export TF_VAR_tofu_state_passphrase=$(op read "op://Homelab/tofu state passphrase/password")

cd tofu/admin/cluster
tofu init
tofu workspace select alfheim
tofu plan

cd ../../workloads/alfheim/platform
tofu init && tofu plan
```

## Status / TODO

**Credentials** (blocks `tofu apply` and any real Ansible run)

- [x] Decide the 1Password vault (`Homelab`, in `cluster.yaml`)
- [x] Create `homelab control node SSH key` (SSH Key), `hugin PowerDNS API key` (API Credential)
- [ ] Set `OP_SERVICE_ACCOUNT_TOKEN` wherever `tofu`/`ansible` actually run from
- [ ] Put the state/plan encryption passphrase in 1Password as `tofu state passphrase`
- [ ] After the reinstall: create `alfheim Proxmox root token` as a **Password** item (see the install doc). Every other token is created by OpenTofu and written to 1Password automatically.

**Before the rebuild**

- [x] Fill the `REPLACE_ME`s in `bootstrap/proxmox/answer.toml`
- [x] Find the NVMe's `/dev/disk/by-id` path (`ansible-playbook discover-hardware.yml`) and put it in `cluster.yaml`
- [x] Confirm `mimir`'s NFS export path and that it's reachable from the servers VLAN - the image datastore depends on it
- [x] Confirm `eno1`/`eno2` are offered no DHCP lease, or cable only `eno3` for the install

**OpenTofu**

- [ ] Decide the state backend (R2 vs S3) - local-only right now
- [ ] Pin a checksum on the Debian cloud image - `latest` moves
- [ ] ACME account + node certificates, then drop `insecure = true` from every layer

**Ratatoskr (Raspberry Pi)**

- [x] Flash the SD card for real - not hands-on verified against real Pi 3B hardware
- [x] Add its DHCP reservation once it's on the network

**BGP / DNS**

- [x] Write and upload the UDM Pro's own FRR config (already drafted)
- [x] Confirm real ECMP behaviour against the actual UDM Pro firmware in practice

**Other**

- [ ] Reboot-required alerting - needs a monitoring stack
- [ ] CI: static checks on a hosted runner, a self-hosted runner for anything touching
      the LAN
- [ ] Everything cluster/Talos-related - arrives as `tofu/workloads/alfheim/talos/` with its own providers, plus a `workloads.talos` grant in the cluster file carrying its `csi` and `ccm` integrations (commented out there already)
- [ ] Network config for the NAS hosts - addressed in `ipam.yaml`, not yet applied
- [ ] `ipam.yaml` still marks every host and site `status: planned`, which several no longer are. Nothing reads the field (`tools/generate.py` filters on `role`), so deleting it may beat maintaining it.
