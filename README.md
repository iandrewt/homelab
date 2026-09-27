# homelab

Infrastructure-as-code for my home network and home lab (site `alfheim`): OpenTofu builds the Proxmox cluster and its guests, Ansible configures the hosts, and NetBox is the source of truth for every address either of them uses.

## Layout

```
tools/        the `homelab` CLI: reads NetBox, renders what can't read it, syncs DNS
ansible/      roles, playbooks, and an inventory read from NetBox
tofu/         modules, one file per cluster, one directory per privilege layer
unifi/        generated config for the UDM Pro, which nothing here can apply directly
```

```
tofu/clusters/
  alfheim.yaml            cluster settings read by admin layers
  schema.json
tofu/admin/
  root/                   root@pam only - the tofu@pve user, ACME
  access/                 roles, users, pools, realms, every other token
  cluster/                cluster options, SDN, node firewall, storage, mappings
  images/                 cloud image downloads
tofu/workloads/
  alfheim/network/dns/    hugin, in the Network team's `network/dns` pool
unifi/generated/
  alfheim-frr.conf        BGP toward the resolvers, written by `homelab generate`
```

The tofu is shaped in a multi-cluster way to make it easier for others to re-use. To use it, just replace `alfheim.yaml` with your own cluster info.

Workloads are managed separately to the cluster itself. The `tenants` map in the cluster YAML is how you can separate groups of workloads: it gets a pool, a PVE user of its own, a token written to 1Password, and the VNets it may join. The tenant then makes its own workloads as states under `tofu/workloads/<site>/<tenant>/`, organised as it likes - VMs straight in its pool, or in pools of its own under it - and nothing in the cluster YAML changes for a new one. Anything running *inside* a workload that needs the Proxmox API itself is an `integration` under its tenant, and gets its own user, token and roles.

## Using this repo

Everything that needs an address reads NetBox directly, each consumer with its own
read-only token from a 1Password API Credential item.

The Ansible inventory plugin won't template its endpoint, so that's the one thing the
environment still carries:

```
export NETBOX_API=https://netbox.example
```

```
uv run homelab generate                 # rewrite unifi/generated/; --check reports drift instead
uv run homelab network-config alfheim   # the network-config for a site's install media
```

CI can't reach NetBox, so it checks none of this - run `generate --check` against a
reachable instance to find FRR drift.

### Ansible

There is no inventory file. [`ansible/inventory/netbox.yml`](ansible/inventory/netbox.yml)
builds it from NetBox: active or staged hosts tagged `ansible-managed`, grouped by device
role and platform into the groups `site.yml` plays against. What each host needs to know
about its site comes from [`ansible/vars_plugins/netbox.py`](ansible/vars_plugins/netbox.py),
which loads the same model `homelab generate` renders from - so Ansible runs through `uv`:

```
uv run ansible-galaxy collection install -r ansible/requirements.yml
cd ansible && uv run ansible-playbook site.yml --limit <hostname>
cd ansible && uv run ansible-playbook site.yml --limit <hostname> --skip-tags cis   # routine runs
cd ansible && uv run ansible-playbook site.yml --limit <hostname> --tags resolvers  # one play
cd ansible && uv run ansible-playbook full-upgrade.yml
```

### OpenTofu

OpenTofu needs no wrapper. State encryption goes through `homelab tofu-key`, an
[external key provider](https://opentofu.org/docs/language/state/encryption/) that
fetches the passphrase from 1Password itself

```
export OP_SERVICE_ACCOUNT_TOKEN=...    # or OP_ACCOUNT=<name> for the desktop app

tofu -chdir=tofu/admin/cluster init
tofu -chdir=tofu/admin/cluster workspace select alfheim
tofu -chdir=tofu/admin/cluster plan

tofu -chdir=tofu/workloads/alfheim/network/dns plan
```

Set `HOMELAB_STATE_PASSPHRASE` to bypass 1Password entirely - that is how CI validates a
configuration it will never apply.

The `root` layer is the exception: it authenticates as `root@pam` with the install
password from `PROXMOX_VE_PASSWORD` rather than a token, so there is no bootstrap token
to create by hand.

## Status / TODO

**Credentials** (blocks `tofu apply` and any real Ansible run)

- [x] Decide the 1Password vault (`Homelab`, in `alfheim.yaml`)
- [x] Create `homelab control node SSH key` (SSH Key), `alfheim PowerDNS API key` (API Credential)
- [ ] Set `OP_SERVICE_ACCOUNT_TOKEN` wherever `tofu`/`ansible` actually run from
- [x] Put the state/plan encryption passphrase in 1Password as `Tofu State Passphrase` (read by `homelab tofu-key`)

**Before the rebuild**

- [x] Fill the `REPLACE_ME`s in `bootstrap/proxmox/answer.toml.tmpl`
- [x] Find the NVMe's `/dev/disk/by-id` path (`ansible-playbook discover-hardware.yml`) and put it in `alfheim.yaml`
- [x] Confirm `mimir`'s NFS export path and that it's reachable from the servers VLAN - the image datastore depends on it
- [x] Confirm `eno1`/`eno2` are offered no DHCP lease, or cable only `eno3` for the install

**OpenTofu**

- [ ] Decide the state backend (R2 vs S3) - local-only right now
- [ ] Pin a checksum on the Debian cloud image - `latest` moves
- [ ] ACME account + node certificates, then drop `insecure = true` from every layer

**Ratatoskr (Raspberry Pi)**

- [x] Flash the SD card for real - not hands-on verified against real Pi 3B hardware
- [x] Add its DHCP reservation once it's on the network
- [x] Record its reserved IPv4 in NetBox (status `dhcp`), plus `lo` and its `dns` loopbacks
- [ ] Drop the forky pin for `src:audit` once trixie ships audit 4.0.5 or later (linux-audit/audit-userspace#496)
- [x] Reinstall from the new seed once `homelab validate` passes: EUI-64, and the lease's DNS
      rather than the VIP it is about to serve

**BGP / DNS**

- [x] Write and upload the UDM Pro's own FRR config (now generated into `unifi/generated/`)
- [x] Confirm real ECMP behaviour against the actual UDM Pro firmware in practice

**DNS**

- [x] Zone TTLs, and the same as `netbox_dns` plugin defaults
- [x] Once both resolvers are up, set UniFi's DHCP DNS and RA RDNSS to the VIP on every internal network; until then clients stay on the UDM
- [x] UniFi DHCP option 119 per network, per the search list in the DNS plan
- [x] BGP peer groups and listen sessions in NetBox, replacing the `bgp-listen` tag and the `router` ASN role
- [ ] IPv6 BGP transport once the UDM's kernel is 5.2 or later
- [x] octoDNS from `netbox_dns` to PowerDNS, replacing the role's RRset provisioning
- [ ] Ask the ISP to delegate alfheim's /48 reverse zone to Route53; a `public` view for it and `titmuss.au`
- [ ] DNSSEC stage 1: sign internal zones, trust anchors on both recursors, validation on
- [ ] DNSSEC stage 2: public copies in Route53, two DS records at Cloudflare, ACME CNAMEs into `acme.titmuss.au`
- [x] Laptop: positive trust anchors for the internal zones until stage 2

**Other**

- [ ] Reboot-required alerting - needs a monitoring stack
- [ ] CI: static checks on a hosted runner, a self-hosted runner for anything touching
      the LAN
- [ ] Everything cluster/Talos-related - arrives as `tofu/workloads/alfheim/platform/talos/` with its own providers, plus a `tenants.platform` grant in the cluster file carrying its `csi` and `ccm` integrations (commented out there already)
- [x] Network config for the NAS hosts - addressed in NetBox, not yet applied

**NetBox data**

- [x] Rename devices: drop `alfheim-`, `<role-code>-<nn>` sequenced per site, pets by hostname
- [x] Fix `ratataskor` → `ratatoskr` on its address's DNS name
- [x] Give `yggdrasil` a named address - `homelab validate`'s node check needs one
- [x] Roles: `Hypervisor` (no "Node"), `Wall Plate`, `Power Point`, `Power Circuit`; `ap-01` back to `Access Point`
- [x] Platform tree via `parent`: Linux › Debian › Debian 13 › Proxmox VE 9.2 / Raspberry Pi OS 13
- [x] Tag Ansible-managed hosts `ansible-managed`
- [x] Put `yggdrasil` in the `alfheim` virtualization cluster
- [x] iDRACs: `mgmt_only` interface + OOB IP on each server, service tag in `serial`
- [x] Keystones as modules on the plates and `pp-a`; TIA-606 link IDs (`A07`) as cable and port labels
- [x] Power feed named as printed (`N3`); `circuit-n3` stays as the fan-out
- [x] Tenant on BGP/LB prefixes (network vs platform)
- [x] Planned custom fields replaced: ASN roles, a `role` field on `netbox_bgp` communities,
      a `bgp-listen` VLAN tag, `netbox_dns` zones
- [x] Renumber hub-shared so `dns` is its base `/26` and the VIP is `10.176.192.53`
- [x] `netbox_dns` zones, name server, and the default view on the site's top-level prefixes
- [x] Match UniFi device names to NetBox

**Ansible**

- [x] Add `netbox.netbox` to `requirements.yml`
- [x] Create read-only NetBox users `homelab`, `tofu` and `ansible`; store each token as `<user> NetBox API token` (API Credential in `Homelab`, URL in `hostname`)
- [x] Check `role`/`platform` hostvar names with `ansible-inventory --list` against live data
- [x] Decide whether Proxmox keeps plain `DEBIAN13-CIS` or gets its own group

**homelab CLI**

- [ ] `bootstrap pi <name>`: look the Device up (site, platform) instead of `--hostname`/`--site`
- [x] Confirm `match: {name: e*}` binds on a Pi's first boot (`cloud-init.log`, `ip -6 addr`)

**Writers into NetBox**

- [ ] UniFi → NetBox discovery: observed addresses and MACs, joined on MAC; first contact by DHCP hostname for `staged` Devices
- [ ] Proxmox → NetBox sync for every VM; Proxmox tags → role/tags; flag duplicate or out-of-prefix MACs
- [ ] A NetBox user per writer, object permissions scoped to what it writes
- [ ] Webhook receiver inside the network: UniFi Alarm Manager → journal entries (never `status`), NetBox → reconcile runs

**OpenTofu / Proxmox**

- [x] Move `hugin` to its own network-owned workload state
- [x] Publish NetBox VLANs as SDN VNets from the admin layer, then drop NetBox from `workloads/*`
- [x] SDN: subnets from NetBox prefixes, `+sdn/<vnet>-all` IPSets in firewall rules, `isolate-ports` on `dmz`; skip SDN's IPAM/DHCP/DNS
- [ ] Backups: an NFS storage of their own on mimir, on a dataset of their own - not `images`, and not `local`, which every tenant can delete from
- [ ] Talos storage NIC: no default route; NFS exports by the storage prefix, so `SDN.Use` on `storage` is the NAS's access control
- [ ] `linux-vm`: match `e*`, let Proxmox allocate the VMID and MAC
- [ ] Proxmox answer server on the laptop, keyed on the SMBIOS type 1 serial (the service tag), URL baked in with `prepare-iso`

**IMDS (day two)**

- [ ] Proxmox-local IMDS from `/etc/pve`: site data from the admin layer, per-VM data from the VM config
- [ ] Instance identity signed by the node key under the cluster CA
- [ ] user-data write endpoint, authorised by the caller's Proxmox token (`VM.Config.Cloudinit`)

**Docs**

- [x] CLAUDE.md: owning team (network/platform/apps) in the ownership table; one writer per NetBox object
- [ ] CLAUDE.md: the naming scheme - names hold only what no field does, lowercase-hyphen, per-site sequences
- [ ] CLAUDE.md: nothing needs NetBox at runtime, only at plan/apply; a rebuild restores NetBox to the laptop first
- [ ] Rehearse a NetBox restore from the NAS backup onto the laptop

**Later**

- [ ] Netboot for disposable Pis: enlist by board serial, then name - never `ratatoskr`
