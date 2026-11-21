# homelab

Infrastructure-as-code for one home network and its lab (site `alfheim`): OpenTofu builds the Proxmox cluster and its guests, Ansible configures the hosts, and NetBox is the source of truth for every address either of them uses. [`docs/netbox.md`](docs/netbox.md) says what to put in yours.

## Layout

```
tools/        the `homelab` CLI: reads NetBox, renders what can't read it, syncs DNS
ansible/      a tree per team, each with an inventory read from NetBox, and the roles they share
tofu/         a directory per team that owns state, a layer per directory, and modules
kubernetes/   what Flux reconciles, a directory per Talos cluster
unifi/        generated config for the UDM Pro, which nothing here can apply directly
octodns/      octoDNS's config: NetBox's internal view to the one primary, its public view out
netbox/       NetBox's schema, which `homelab netbox apply` puts in any NetBox
docs/         how to stand this up yourself, starting with what goes in NetBox
```

```
tofu/identity/
  tenancy/teams/          the teams: each one's Entra group, 1Password vault and state role
  entra/                  directory (groups, the aws extension), aws (Identity Center's app), apps (one per platform instance)
  aws/identity-center/    permission sets, and each group's assignments
tofu/security/aws/
  guardrails/             the SCPs, RCPs and declarative policies every account runs under
  state/                  the state bucket and its key, and a role per team
  logging/, detection/    the organisation's trail, GuardDuty and Access Analyzer, and their alarms
tofu/platform/
  aws/                    the organisation, its accounts, and each account's baseline
  proxmox/clusters/       a file per Proxmox cluster, read by the admin layers (alf-pve-01.yaml, schema.json)
  proxmox/images/         a pinned image library per site (alfheim.yaml)
  proxmox/admin/          a workspace per cluster (images: per site)
    root/                 root@pam only - the tofu@pve user
    access/               roles, users, pools, the Entra realm, every other token
    cluster/              cluster options, SDN, node firewall, storage, mappings, backups
    images/               the site's library, downloaded once through one of its clusters
  kubernetes/             a workspace per Talos cluster, a file each in clusters/ (au1-k8s-shared.yaml)
tofu/network/
  aws/dns/                titmuss.au in Route53, and its signing key
  dns/                    a workspace per site: the resolvers NetBox records as VMs
tofu/modules/             talos-cluster, fleet-bootstrap, talos-vm, linux-vm, netbox-site, entra-app, ...
kubernetes/
  components/             cluster-neutral: Flux's operator, Cilium and its BGP, Proxmox CCM, Intel's GPU driver, the descheduler, the pools NetBox gives, the host firewall, OIDC RBAC; fleet/, the catalog and the ResourceSet that turns a cluster's list into Kustomizations
  clusters/au1-k8s-shared/ its FluxInstance, and the components it takes from fleet/catalog.yaml
  fleet/au1-k8s-shared/   au1-k8s-shared's ring: the one thing it syncs from Git
unifi/generated/
  <router>-frr.conf       BGP toward the speakers, written by `homelab generate`; never committed
  <site>-ipv6.txt         the policies that send another site's IPv6 to IPv4, likewise
```

The tofu is shaped for n of everything: a layer per kind of thing, a workspace and a data file
per instance. Adding a Proxmox cluster, a Kubernetes cluster or a site's resolvers is a file (or
NetBox records) and `tofu workspace new`.

Teams are defined once, in `tofu/identity/tenancy/teams/`, and vended everywhere: an Entra group,
a state role, and whatever each platform grants them. A Proxmox cluster file's `tenants` says
only what a team gets there - a pool, a token written to 1Password, the VNets it may join - and
the team makes its own workloads under `tofu/<team>/`. Anything running *inside* a workload
that needs the Proxmox API itself is an `integration` under its tenant, with its own user,
token and roles.

Kubernetes clusters run a fleet in rings: CI pushes `kubernetes/` as a signed OCI artifact on
every commit (a release candidate) and every `v*` tag (a release), and each cluster's ring picks
the newest it allows. Promoting is tagging a release canary has run.

## Using this repo

Everything that needs an address reads NetBox directly, each consumer with its own
read-only token from a 1Password API Credential item.

The Ansible inventory plugin won't template its endpoint, so that's the one thing the
environment still carries:

```
export NETBOX_API=https://netbox.example
```

```
uv run homelab netbox plan              # what NetBox's schema lacks; `apply` adds it
uv run homelab generate                 # rewrite unifi/generated/; --check reports drift instead
uv run homelab network-config           # the network-config for install media
uv run homelab unifi apply alfheim vpn  # a step of the gateways' firewall; dry-run until --apply
```

CI can't reach NetBox, so it checks none of this - run `generate --check` against a
reachable instance to find FRR drift.

### Ansible

Each team runs its own hosts from its own tree, as its own NetBox user and from its own vault:
[`ansible/network/`](ansible/network) the hosts NetBox gives Network as tenant, and
[`ansible/platform/`](ansible/platform) what Platform owns and gave no one. There is no
inventory file: a tree's `inventory/netbox.yml` builds it from NetBox, active or staged hosts
tagged `ansible-managed`, grouped by device role into the groups its `site.yml` plays against.
Both apply the baseline, [`ansible/baseline.yml`](ansible/baseline.yml):
[`homelab.linux`](ansible/collections/ansible_collections/homelab/linux), and a collection per
OS, Debian's [`homelab.debian`](ansible/collections/ansible_collections/homelab/debian). What each
host needs to know about its site comes from
[`ansible/vars_plugins/netbox.py`](ansible/vars_plugins/netbox.py), which loads the same model
`homelab generate` renders from - so Ansible runs through `uv`. A tree's secrets are
declared in its `inventory/onepassword.yml` and resolved through the 1Password SDK before any
task runs, so a run asks the desktop app once (`OP_ACCOUNT=<name>`):

```
uv run ansible-galaxy collection install -r ansible/requirements.yml
uv run ansible-galaxy role install -p ansible/roles -r ansible/requirements.yml
uv run ansible-galaxy role install -p ansible/roles -r ansible/collections/ansible_collections/homelab/debian/requirements.yml
cd ansible/network && uv run ansible-playbook site.yml --limit <hostname>
cd ansible/network && uv run ansible-playbook site.yml --limit <hostname> --skip-tags cis   # routine runs
cd ansible/network && uv run ansible-playbook site.yml --limit <hostname> --tags resolvers  # one play
cd ansible/platform && uv run ansible-playbook ../full-upgrade.yml
```

### OpenTofu

OpenTofu needs no wrapper. State and plans are encrypted with OpenTofu's own
[`aws_kms` key provider](https://opentofu.org/docs/language/state/encryption/), under a
key in the same account as the state bucket: State, which only the bootstrap role
administers. Each team's state is reached as that team's role there, which a layer names by
profile - `homelab-state-<team>`, the first directory of its path. Locally, each is the role
assumed from your `TofuState` session in State:

```ini
# ~/.aws/config
[profile homelab-state-network]
role_arn = arn:aws:iam::877127520873:role/tofu-state/tofu-state-network
source_profile = homelab-state
```

CI would define the same profile names from its OIDC token (`web_identity_token_file`)
instead, so the layers don't change. The 1Password provider reads every other secret.

octoDNS writes Route53 as `network/aws/dns`'s `octodns` role, which can't touch a cluster's
names, assumed from your Network session:

```ini
[profile homelab-octodns]
role_arn = <its octodns_role_arn output>
source_profile = homelab-network
```

```
aws sso login
export OP_ACCOUNT=<name>               # the desktop app; a service account token is for CI

tofu -chdir=tofu/platform/proxmox/admin/cluster init
tofu -chdir=tofu/platform/proxmox/admin/cluster workspace select alf-pve-01
tofu -chdir=tofu/platform/proxmox/admin/cluster plan

tofu -chdir=tofu/network/dns workspace select alfheim
tofu -chdir=tofu/network/dns plan
```

The `root` layer is the exception: it authenticates as `root@pam` with the install
password from `PROXMOX_VE_PASSWORD` rather than a token, so there is no bootstrap token
to create by hand.

### Talos

`tofu/platform/kubernetes`, in the cluster's workspace, builds the nodes, applies their
machine config over the Talos API, bootstraps etcd, and installs Cilium, Proxmox CCM and Flux
once, from the files of the artifact the cluster's ring resolves to. From
then on Flux owns everything in the cluster but Talos's own manifests, its own upgrades
included. Each upgrade has one owner:

| | Upgraded by |
|---|---|
| Talos | a class's `talos` version in the cluster's data file, rolled node by node |
| Kubernetes | `kubernetes_version` in the cluster's data file, through `upgrade-k8s` |
| CoreDNS, Talos CCM, Gateway API's CRDs | Talos's own manifests - `gateway_api` in the data file, Talos CCM's chart in `talos-cluster` - re-applied by `upgrade-k8s` |
| Cilium, Proxmox CCM, Flux | their chart versions under `kubernetes/components/`, through Flux, ring by ring |

Cilium is never in Talos's own manifests, so `upgrade-k8s` has nothing of it to put back.
Talos CCM is, because Talos's health checks wait on the kubelet certificates it approves -
so it runs on the host network, before there is a CNI.

A Talos upgrade only rolls one node at a time - drained, rebooted into the new image, and
back - when applied without parallelism:

```
tofu -chdir=tofu/platform/kubernetes workspace select au1-k8s-shared
tofu -chdir=tofu/platform/kubernetes apply -parallelism=1
op read 'op://Platform/Talos Config - au1-k8s-shared/notesPlain' > ~/.talos/config
talosctl kubeconfig                    # break-glass; people sign in with Entra
```

Taking a control plane out of `nodes` destroys its VM, but nothing tells etcd: run
`talosctl -n <node> reset --graceful` first, so it leaves the cluster cleanly.

## Status / TODO

**NetBox data**

- [x] Tag the primary resolver `dns-primary`, and the VLANs that may reach its API `dns-api`

**OpenTofu / Proxmox**

- [ ] Drop `insecure` from each Proxmox cluster file once the private CA issues the nodes' certificates
- [ ] Replace the Kubernetes layer's `external` fleet read: an antipattern
- [x] Backups: an NFS storage of their own on mimir, on a dataset of their own - not `images`, and not `local`, which every tenant can delete from
- [ ] proxmox-csi: a `csi` entry in au1-k8s-shared's `proxmox_integrations` (`pool: [VM.Audit, VM.Config.Disk]`, `storages: {nvme: [Datastore.Allocate, Datastore.AllocateSpace, Datastore.Audit]}`)
- [ ] Karpenter for Proxmox - revisit; Proxmox CCM already gives every node a provider ID

**Kubernetes**

- [ ] Bring up au1-k8s-shared on address plan v2, replacing valhalla
- [ ] Make the repository public, and drop the GHCR pull token
- [ ] The boot service: cached Image Factory artifacts for UEFI HTTP Boot and iPXE, then metal nodes
- [ ] Renovate for what Dependabot can't see: Talos and Kubernetes versions, Flux's charts and sources
- [ ] External Secrets with 1Password - only the GitHub App key and its own token stay bootstrap-managed
- [ ] A self-hosted discovery service, off Sidero's
- [ ] Talos 1.15: `WorkloadResourceConfig` memory caps, and drop the explicit `lockdown=integrity`
- [ ] Envoy's xDS back to ADS once cilium/cilium#49181 is fixed
- [ ] Drop the host firewall's exception for replies to Envoy once cilium/cilium#45565 is fixed
- [ ] LLDP on the Talos nodes (`siderolabs/lldpd`) once Cilium's host firewall passes it - cilium/cilium#17877
- [ ] Confirm a DHCP lease renews through the host firewall

**Ratatoskr (Raspberry Pi)**

- [ ] Drop the forky pin for `src:audit` once trixie ships audit 4.0.5 or later (linux-audit/audit-userspace#496)

**BGP / DNS**

- [ ] IPv6 BGP transport once the UDM's kernel is 5.2 or later
- [ ] BFD to the UDM once its FRR ships bfdd - check with UniFi OS 6; until then, timers 3 9
- [ ] Ask the ISP to delegate alfheim's reverse zone to the Route53 zone `homelab dns apply` makes for it

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
