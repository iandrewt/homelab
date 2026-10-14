# homelab

Infrastructure-as-code for my home network and home lab (site `alfheim`): OpenTofu builds the Proxmox cluster and its guests, Ansible configures the hosts, and NetBox is the source of truth for every address either of them uses.

## Layout

```
tools/        the `homelab` CLI: reads NetBox, renders what can't read it, syncs DNS
ansible/      roles, playbooks, and an inventory read from NetBox
tofu/         a directory per team that owns state, a layer per directory, and modules
kubernetes/   what Flux reconciles, a directory per Talos cluster
unifi/        generated config for the UDM Pro, which nothing here can apply directly
```

```
tofu/platform/
  aws/                    the organisation, identity, the state bucket and its key, logging
  proxmox/clusters/
    alfheim.yaml          cluster settings read by admin layers
    schema.json
  proxmox/admin/          one workspace per cluster
    root/                 root@pam only - the tofu@pve user, ACME
    access/               roles, users, pools, realms, every other token
    cluster/              cluster options, SDN, node firewall, storage, mappings, backups
    images/               cloud image and Talos ISO downloads
  proxmox/alfheim/valhalla/  the valhalla Talos cluster, through Flux's bootstrap
tofu/network/
  aws/dns/                the public copies of internal zones, in Route53
  proxmox/alfheim/dns/    hugin, in the Network team's `network/dns` pool
tofu/modules/
kubernetes/clusters/valhalla/
  flux-system/            the FluxInstance and flux-operator, which Flux upgrades itself
  infrastructure/         controllers (Cilium, Proxmox CCM, the GPU plugin), then their configs
  apps/
unifi/generated/
  alfheim-frr.conf        BGP toward the resolvers, written by `homelab generate`
```

The tofu is shaped in a multi-cluster way to make it easier for others to re-use. To use it, just replace `alfheim.yaml` with your own cluster info.

Workloads are managed separately to the cluster itself. The `tenants` map in the cluster YAML is how you can separate groups of workloads: it gets a pool, a PVE user of its own, a token written to 1Password, and the VNets it may join. The tenant then makes its own workloads as states under `tofu/<tenant>/proxmox/<cluster>/`, organised as it likes - VMs straight in its pool, or in pools of its own under it - and nothing in the cluster YAML changes for a new one. Anything running *inside* a workload that needs the Proxmox API itself is an `integration` under its tenant, and gets its own user, token and roles.

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
which loads the same model `homelab generate` renders from - so Ansible runs through `uv`.
Its secrets are declared in [`ansible/inventory/onepassword.yml`](ansible/inventory/onepassword.yml)
and resolved through the 1Password SDK before any task runs, so a run asks the desktop app
once (`OP_ACCOUNT=<name>`):

```
uv run ansible-galaxy collection install -r ansible/requirements.yml
cd ansible && uv run ansible-playbook site.yml --limit <hostname>
cd ansible && uv run ansible-playbook site.yml --limit <hostname> --skip-tags cis   # routine runs
cd ansible && uv run ansible-playbook site.yml --limit <hostname> --tags resolvers  # one play
cd ansible && uv run ansible-playbook full-upgrade.yml
```

### OpenTofu

OpenTofu needs no wrapper. State and plans are encrypted with OpenTofu's own
[`aws_kms` key provider](https://opentofu.org/docs/language/state/encryption/), under a
key in the same account as the state bucket. Each team's state is reached as that team's
role in Platform, which a layer names by profile - `homelab-state-<team>`, the first
directory of its path. Locally, each is the role assumed from your `TofuState` session:

```ini
# ~/.aws/config
[profile homelab-state-network]
role_arn = arn:aws:iam::585242447535:role/tofu-state/tofu-state-network
source_profile = homelab-state
```

CI would define the same profile names from its OIDC token (`web_identity_token_file`)
instead, so the layers don't change. The 1Password provider reads every other secret.

```
aws sso login
export OP_ACCOUNT=<name>               # the desktop app; a service account token is for CI

tofu -chdir=tofu/platform/proxmox/admin/cluster init
tofu -chdir=tofu/platform/proxmox/admin/cluster workspace select alfheim
tofu -chdir=tofu/platform/proxmox/admin/cluster plan

tofu -chdir=tofu/network/proxmox/alfheim/dns plan
```

The `root` layer is the exception: it authenticates as `root@pam` with the install
password from `PROXMOX_VE_PASSWORD` rather than a token, so there is no bootstrap token
to create by hand.

### Talos

`tofu/platform/proxmox/alfheim/valhalla` builds the nodes, applies their machine config
over the Talos API, bootstraps etcd, and installs Cilium, Proxmox CCM and Flux once. From
then on Flux owns everything in the cluster but Talos's own manifests, its own upgrades
included. Each upgrade has one owner:

| | Upgraded by |
|---|---|
| Talos | a class's `talos` version in `nodes.tf`, rolled node by node |
| Kubernetes | `kubernetes_version` in `talos.tf`, through `upgrade-k8s` |
| CoreDNS, Talos CCM | Talos's own manifests - Talos CCM's chart version in `talos.tf` - re-applied by `upgrade-k8s` |
| Cilium, Proxmox CCM, Flux | their chart versions under `kubernetes/clusters/valhalla/`, through Flux |

Cilium is never in Talos's own manifests, so `upgrade-k8s` has nothing of it to put back.
Talos CCM is, because Talos's health checks wait on the kubelet certificates it approves -
so it runs on the host network, before there is a CNI.

A Talos upgrade only rolls one node at a time - drained, rebooted into the new image, and
back - when applied without parallelism:

```
tofu -chdir=tofu/platform/proxmox/alfheim/valhalla apply -parallelism=1
op read 'op://Platform/Talos Config - valhalla/notesPlain' > ~/.talos/config
talosctl kubeconfig                    # break-glass; people sign in with Entra
```

Taking a control plane out of the map destroys its VM, but nothing tells etcd: run
`talosctl -n <node> reset --graceful` first, so it leaves the cluster cleanly.

## Status / TODO

**NetBox data**

- [x] Tag the primary resolver `dns-primary`, and the VLANs that may reach its API `dns-api`

**OpenTofu / Proxmox**

- [ ] Pin a checksum on the Debian cloud image - `latest` moves
- [ ] Delete `proxmox-acme-<cluster>` and its access key once an internal CA issues the nodes' certificates
- [x] Backups: an NFS storage of their own on mimir, on a dataset of their own - not `images`, and not `local`, which every tenant can delete from
- [ ] Pin a checksum on the Talos ISO
- [ ] Entra app registrations into tofu - Proxmox's and `Kubernetes valhalla`
- [ ] proxmox-csi, with its `csi` integration commented out in the cluster file
- [ ] Karpenter for Proxmox - revisit; Proxmox CCM already gives every node a provider ID

**Kubernetes**

- [ ] Bring up valhalla: the valhalla layer applied, Flux reconciling, the checks in its plan passed
- [ ] Renovate for what Dependabot can't see: Talos and Kubernetes versions, Flux's charts and sources
- [ ] External Secrets with 1Password - only the GitHub App key and its own token stay bootstrap-managed
- [ ] A self-hosted discovery service, off Sidero's
- [ ] Talos 1.15: `WorkloadResourceConfig` memory caps, and drop the explicit `lockdown=integrity`

**Ratatoskr (Raspberry Pi)**

- [ ] Drop the forky pin for `src:audit` once trixie ships audit 4.0.5 or later (linux-audit/audit-userspace#496)

**BGP / DNS**

- [ ] IPv6 BGP transport once the UDM's kernel is 5.2 or later
- [ ] Ask the ISP to delegate alfheim's reverse zone to Route53; a `public` view for it and `titmuss.au`

**homelab CLI**

- [ ] `bootstrap pi <name>`: look the Device up (site, platform) instead of `--hostname`/`--site`

**Writers into NetBox**

- [ ] UniFi → NetBox discovery: observed addresses and MACs, joined on MAC; first contact by DHCP hostname for `staged` Devices
- [ ] Proxmox → NetBox sync for every VM; Proxmox tags → role/tags; flag duplicate or out-of-prefix MACs
- [ ] A NetBox user per writer, object permissions scoped to what it writes
- [ ] Webhook receiver inside the network: UniFi Alarm Manager → journal entries (never `status`), NetBox → reconcile runs

**IMDS (day two)**

- [ ] Proxmox-local IMDS from `/etc/pve`: site data from the admin layer, per-VM data from the VM config
- [ ] Instance identity signed by the node key under the cluster CA
- [ ] user-data write endpoint, authorised by the caller's Proxmox token (`VM.Config.Cloudinit`)

**Other**

- [ ] Reboot-required alerting - needs a monitoring stack
- [ ] CI: static checks on a hosted runner, a self-hosted runner for anything touching the LAN; 1Password service accounts per team (read-only `Network`, read-write `Platform`), and an OIDC trust statement per team state role
- [ ] CLAUDE.md: the naming scheme - names hold only what no field does, lowercase-hyphen, per-site sequences
- [ ] CLAUDE.md: nothing needs NetBox at runtime, only at plan/apply; a rebuild restores NetBox to the laptop first
- [ ] Rehearse a NetBox restore from the NAS backup onto the laptop
- [ ] Netboot for disposable Pis: enlist by board serial, then name - never `ratatoskr`
