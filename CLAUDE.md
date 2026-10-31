# homelab

Infrastructure-as-code for one home network (site `alfheim`, with `vanaheim` as a branch):
OpenTofu builds the Proxmox cells, Kubernetes clusters and guests, Ansible configures the hosts,
and **NetBox** is the source of truth for every address either of them uses. Breaking changes
are fine; migration shims are not.

## NetBox

**NetBox is the only place an address is decided, and everything reads it directly.** Its
*schema* - the Roles, tags, custom fields and policies every consumer reads it by - is the
repo's, in `netbox/schema.yaml`, which `homelab netbox apply` enforces; its *data* never is.
`docs/netbox.md` says what the data must hold.

| Layer | Reads it through |
|---|---|
| OpenTofu | the `netbox` provider, via `tofu/modules/netbox-site` |
| Ansible | `netbox.netbox.nb_inventory` for hosts, `vars_plugins/netbox.py` for site facts |
| the UniFi gateways | `homelab unifi`, for their firewall, through Site Manager; `unifi/generated/<router>-frr.conf`, pasted in by hand, for BGP |

- Only `admin/cluster` (VNets, subnets, firewall sources) and a team's workloads (a cluster's
  networks, pools and API address; the DNS fleet's VMs) need NetBox. `root`, `access` and
  `images` take the API endpoint from the cluster file, and plan with NetBox down.
- There is no inventory file: `nb_inventory` builds it from device roles, platforms and sites.
- CI can't reach NetBox, so it checks none of this; run `homelab generate --check` against a
  reachable instance.
- **Look objects up by natural key, never by ID**: the instance moves (laptop, hosted,
  restored). `netbox.py` fetches each collection in bulk and joins on prefixes, vids, slugs and
  names. A device's name is unique only within its site, and a nested host carries no site, so
  an address joins its host by ID within one fetch, never across them.
- **A site is its slug** everywhere in the repo; its NetBox name is where it is, never committed.
- Each consumer is a NetBox user of its own (`schema.yaml`'s `users`, applied with `--identity`
  and an admin's token), with a read-only token in an API Credential: `NetBox API Token -
  Homelab` (the CLI; `NETBOX_ITEM_REF` overrides it) and `- Ansible` in `Network`, `- Tofu` in
  both `Platform` and `Network`. The token is `credential` and the URL `hostname`, since
  OpenTofu's ephemeral item reads no custom fields. `nb_inventory` alone also needs `NETBOX_API`.
- Site facts reach Ansible as `netbox_site` (plus `netbox_sites`, `netbox_zones`): the model's
  own dicts, from one `model.load()` per run. So Ansible runs under `uv run`.

Each fact lives on the object NetBox or a plugin already has for it. A custom field exists only
where NetBox has nothing, and points at a built-in object rather than a choice:

| Fact | Where |
|---|---|
| a segment (a tier crossed with a kind) | Prefix, role `<tier>-<kind>s`: `site-services`, `home-instances`, `global-services`... |
| a purpose's pool | Prefix, role `<purpose>`, inside a segment |
| a speaker group | ASN, assigned to the site, role `dns-fleet` or `kubernetes-cluster`; custom field `cluster` if it's a cluster's |
| what a speaker group may originate | Prefix, custom field `origin` → its ASN (a dynamic pool); IP, custom field `origins` (an assigned address, always a `/32` or `/128`) |
| who changes a record, and whose capacity it is | NetBox's Owner (a team) and Tenant (the team it's carved out for) |
| a cloud region | Site Group (`au1`), holding the site its AZs are at; a site in none is a branch. Regions stay geographic |
| an AZ | Location (`alf-az1`), the rooms with capacity in one failure domain nested under it |
| a cluster | Virtualization Cluster: a Proxmox cell scoped to its AZ, named as gear (`alf-pve-01`); a Kubernetes cluster scoped to its region, `<region>-<purpose>` (`au1-shared`), custom field `number` its Cilium cluster ID, unique within a mesh. Its node or corosync network is the VLAN whose `cluster` names it |
| a CPU family | the host's CPU module; a VM inherits its host's, under `cpu=host` |
| the site's router | ASN, role `router`, assigned to the site; the Device, role `Router Firewall` (by name: the tofu provider can look a role up by nothing else) |
| the VIDs a site may have | its VLAN group's `vid_ranges`: below 100 anywhere, 200-399 and the lab's trunks at a hub |
| a VLAN's gateway | the IP on the router's interface inside the VLAN's prefix; none on a VLAN-only network |
| how the router peers | the BGP plugin: a peer group per listen range, holding its timers and export policy (`NOTHING`); a session per speaker group allowed on a range - the router's device and address there, the group's ASN, the range as remote prefix, the group's prefix limit |
| what a guest's NIC joins | VLAN, custom field `vnet` - its SDN VNet in the zone the site's slug names |
| whether its guests can reach each other | VLAN, tag `isolate-ports` |
| what a source is | its zone: a VLAN's Role, or custom field `zone` (→ a Role) on a routed prefix or a block holding one zone's networks |
| who may reach a destination | its audience: custom fields `audience` (→ zone Roles, and `internet`), `audience_tags`, `audience_sites`, on a prefix - the default for what's on it - or on a Service where a port needs another. `netbox-site`'s `audiences` and `model.sources` expand it alike |
| a Service's ports | a Service Template |
| an address many hosts serve | an FHRP group, protocol `other`: the anycast addresses on it, the interfaces serving them (`anycast0`) its members, its Services on it. The recursor, and each cluster's API |
| a source narrower than a zone | a trust tag (`connector`, `breakglass`, `proxmox-api`, `nas-api`, `bmc-control`, `storage-client`) on a sub-pool, a VLAN, or a device or VM (covering its primary addresses): a rule names the tag, never an address |
| a BMC | its device's `oob_ip`, named `idrac-<service tag>` |
| a host's SSH host key | Device or VM, custom field `ssh_host_key`: made on the host, recorded by `homelab pin ssh` once checked. Ansible connects by primary address and takes no other key |
| what a host admits | its Services, which `host_firewall` makes its nftables from; nothing else comes in |
| a Service's certificate | Service, custom field `certificate`: made on the host, recorded by `homelab pin` from a fingerprint read over SSH. Its clients trust nothing else |
| a resolver | Device or VM, role `dns-resolver`, holding its authoritative address per family, named `<host>.titmuss.zone` as its NS records name it; a VM's size, cluster, host and VLAN are what `network/dns` builds |
| the recursor | FHRP group `recursor`, the global `dns-recursor` pool's address, `dns.titmuss.zone`, originated by every fleet |
| which view names an address | the view holding the longest prefix with any: `_default_` the home blocks, `public` the nameservers' and the recursor's pools, so their glue follows NetBox. netbox_dns writes a PTR only in its forward record's view, so those PTRs are stated |
| what DHCP hands out | the DHCP plugin: a server per site router, a subnet per IPv4 network with its pool, lease time and options. No reservations: a rule names a host by its EUI-64 |
| the zones' one primary | that Device or VM, tag `dns-primary` |
| zones and records | `netbox_dns`, which writes A/AAAA/PTR from each IP's `dns_name` |
| a device's name | a pet from a theme (RFC 1178: mimir, yggdrasil); gear `<site code>-<role>-<nn>` (`alf-gw-01`), the code in the Site's `facility`; the plant as TIA-606 labels it - a panel by its letter (`alf-pp-a`), a jack by the panel port it lands on (`alf-A23`), power by its circuit (`alf-N3-01`) |
| a name | by what answers for it: what the cloud runs at a site `<host>.<site>.infra.titmuss.zone`; an address that exists for a service `<resource>.<service>.<region>.sys.titmuss.zone`, a CNAME where it's a machine's interface; the home's devices `<device>.<site>.home`; a team's `<team>.int`, by its own key; a client `clients.<site>.home`, never trusted and never a target |
| a zone whose owner writes its records | `netbox_dns` zone, tag `rfc2136`, its tenant the owning team: octoDNS leaves it alone |
| whether a zone is signed | `netbox_dns` DNSSEC policy on the zone |

A **purpose is a prefix Role**: adding one is a Role and a prefix, and no list in the code
changes. Descriptions are short noun phrases that stand alone, empty where the name says it.

## Ownership

Each file is a contract for one thing, and nothing reaches across:

| Source | Owns |
|---|---|
| NetBox | addresses, prefixes, VLANs, and what each machine is |
| `tofu/identity/tenancy/teams/<team>.yaml` | which teams exist: each one's Entra group, 1Password vault and state role. Never who is in them - that's Entra's |
| Entra (`tofu/identity/entra/*`) | people and their groups; an app per platform instance, vended from its data file |
| `tofu/platform/proxmox/clusters/<name>.yaml` | a Proxmox cell: nodes, storage, MAC prefix, the API endpoint, what it grants each tenant |
| `tofu/platform/proxmox/images/<site>.yaml` | the site's pinned image library, on storage every cell there mounts |
| `tofu/platform/kubernetes/clusters/<name>.yaml` | a Kubernetes cluster: versions, node classes, VMs, the cells it may use, the integrations it asks them for |
| `kubernetes/fleet/<name>/` | the ring a cluster runs, and nothing else Flux reads from Git |
| `tofu/<team>/<kind>/` | a team's workloads, a layer per kind with a workspace per instance |

The API `endpoint` is in the cluster file, not NetBox: it's a name, which a re-IP doesn't change.
Search domains, like resolvers, are UniFi's, per network (option 119).

## Addressing

**Nothing is derived.** NetBox states each prefix and address, and `model.py` joins rather than
computes; what a prefix must satisfy is NetBox's own containment. The design is in
`internal/design/address-plan.md`, locations and names in `internal/design/location-plan.md`.

- A **class** (land, cloud, lab) is a container prefix; a purpose is a **role** on a prefix. A
  prefix carries a VLAN or a role, rarely both.
- An address belongs to a site when it falls **inside one of that site's prefixes**; a VLAN's
  gateway is the router's address inside its prefix, never the first one in it.
- A site's VLANs are the **active ones in the VLAN group named by its slug**. `model.py` and
  `netbox-site` both join that way, and have to agree.
- **Zones are NetBox's**, and so are their records. A site's forward zone is
  `<slug>.infra.titmuss.zone`. Only the `_default_` view is internal; a zone of the same name in
  another view is somebody else's copy.
- **octoDNS writes the records** (`homelab dns`) from the internal view to the one primary,
  deletes included, through its API's HAProxy and the certificate NetBox pins (the API itself
  is on loopback); every other server replicates. Ansible only creates zones, with NetBox's SOA
  (octoDNS would use PowerDNS's default), so a new zone needs the resolvers play before its first
  sync. A zone tagged `rfc2136` is its owner's - a cluster's `apps.<name>.k8s.<region>.sys`, a
  team's `<team>.int`, a site's `clients.<site>.home` - and `homelab dns` lists every other zone
  by name, so octoDNS never sees it. A zone holding another's delegation publishes its DS, read
  from the primary. The `public` view goes to Cloudflare (`titmuss.house`, `titmuss.zone`) and
  Route53 (`titmuss.au`), where the ownership processor leaves the platforms' names alone.
- **A zone is signed while its DNSSEC policy is active.** PowerDNS honours only the policy's key
  templates, with NSEC; leave NSEC3, CDS/CDNSKEY and every timing unset. Keys are made once and
  rolled by hand; `homelab dns keys backup` copies them to `Break-glass`, and a rebuilt primary
  imports them (`homelab dns keys restore`), so every DS holds. Every resolver trusts the DS the control node reads from the primary each run,
  and gives an unsigned internal zone a negative anchor. For a client validating itself, `infra`,
  `sys`, `home` and `int` are secure delegations from Cloudflare's `titmuss.zone` to
  `hugin.titmuss.zone` and `ratatoskr.titmuss.zone`, and each delegates its children the same
  way, so a new site, region or team never touches Cloudflare.
- **The default is DHCP**: DHCPv4 and SLAAC, resolvers and search list from the lease, so an
  install seed is the same for every host at a site. A resolver alone ignores the lease's
  servers and asks its VLAN's gateway, since nothing it needs to build itself may come from the
  VIP it will serve; `linux-vm` gives hugin the same.
- **A leased address is status `dhcp`**, recorded once leased, never decided ahead; `active` is
  what a host configures itself, `reserved` held and unused. The only static addresses are
  Proxmox nodes', gateways and routed service addresses; on a VLAN-only network each interface's
  is its EUI-64.

## BGP

Routed blocks carry no L2 and are originated by BGP, laid out **tier → kind → speaker group →
purpose**. A **speaker group** shares an ASN: a DNS fleet, a Kubernetes cluster, the router.
**Only platforms speak BGP**: a tenant's routed address is a platform's load balancer.

- The router **accepts a block** (`permit <block> le 32`) and **listens on a range** (`bgp listen
  range`): Services, and one summary of every cluster's node network. A new speaker or cluster
  needs only a session for its ASN.
- **Acceptance is bound to the route, not the peer**: a peer group per listen range, `remote-as
  external`. A group whose `tcp_md5` is set has a password per site (`BGP Password - <site>
  <group>`, in Network), which every speaker on its ranges must sign with: a Raspberry Pi's
  kernel can't, so Services has none. A route is accepted only from a group with a session on that range, inside
  something it originates, *and* with it first in its AS path (`^<asn>_`) - so a node claiming
  the DNS fleet's ASN still can't originate the recursor, and a route Talos carries on from
  Cilium still names the cluster first. All generated from NetBox. No communities.
- **ASNs are 32-bit, `42CSSGGNNN`**: class, site, group, instance, from a NetBox ASN range per
  class. The router is group 00; Kubernetes' internal instances are the cluster's +1 (Talos's
  workload VRF) and +2 (Cilium).
- **An FRR speaker's one IPv4 session carries both families**, since FRR sets an IPv6 route's
  next hop to the interface's global address. Talos's GoBGP can't (its IPv6 next hop over IPv4
  is IPv4-mapped, which the UDM won't install), so a listen range has an IPv6 twin carrying IPv6
  only, Talos peers with both gateways, and a mapped next hop is denied. `extended-nexthop` waits
  on the UDM's 4.19 kernel. **Policy is required at both ends**: each direction that should carry
  nothing says `route-map NOTHING`. GTSM is off: Talos can't set a TTL of 255.
- **A speaker replies from its routed addresses through the router**: a source rule sends them
  to a table holding only the default via the VLAN's gateways (`frr_bgp_reply_table`), or a
  same-VLAN client's answer goes on-link and the router's firewall drops half the flow.
  Kubernetes needs no such rule.
- A speaker's **service address** is from its own block, on `lo`; an anycast address is what
  clients are configured with. The resolvers' anycast address is on `anycast0`, held down
  (`activation-mode: manual`) and raised by `anycast-check` only while what it fronts runs
  (`frr_bgp_anycast_needs`) and answers (`frr_bgp_anycast_check`: a signed internal name, and a
  public one while a root server answers), unless drained (`/var/lib/anycast/drain`); it never
  starts what it needs. `import-check` advertises it only while the link is up.
- **Between sites is Site Magic, UniFi's OSPF**, IPv4 alone. UniFi refuses our `router ospf`,
  so a hub's *Redistribute to SD-WAN* (`redistribute bgp`, E1) offers every BGP route, and **a
  tier is reach by firewall**: spokes learn a hub's site segments, and its zone policies drop
  them. A spoke's gateway can speak BGP too (the Express 7 does). Another site's IPv6 is
  reached over the internet, so each site rejects the others' GUAs below an Allow for their
  `internet` audience, and Happy Eyeballs takes IPv4; DNS is never rewritten for it.

## Kubernetes

A cluster is Talos in one region, across its Proxmox cells and its metal, named
`<region>-<purpose>` (`au1-shared`). One layer builds every one, `tofu/platform/kubernetes`, a
workspace and a data file (`clusters/<name>.yaml`) each, through `talos-cluster` and
`fleet-bootstrap`. VMs are the data file's; **metal is NetBox's**: a device in the
virtualization cluster is a node, its interfaces named as Talos names its links. A node class's
**lifecycle**: `permanent` seals volumes to the TPM, so the layer refuses metal NetBox doesn't
record with `secure_boot` and `tpm`; `transient` keys them to the node and is tainted
`node.homelab.io/lifecycle=transient:NoSchedule`.

**Every upgrade has one owner**: Talos is `talos_machine.image`, Kubernetes
`talos_cluster.kubernetes_version`, and Cilium, Proxmox CCM and Flux their HelmReleases. Talos
runs `cni: none` and no kube-proxy. Tofu installs those charts exactly once, through
flux-operator-bootstrap's Job, from the files Flux reads - never `helm_release`. Talos CCM is
Talos's: an inline manifest rendered by `helm_template`, since `talos_cluster`'s health checks
wait on the kubelet certificates it approves.

**The bootstrap is defined by rule, so a rebuild works at full capability.** It carries exactly
what Flux can't order with `dependsOn`:

- what Flux needs to run: the CNI, a CCM, routes back to its pods (Cilium's BGP config), the Git
  credentials;
- CRDs a bootstrap-installed controller only looks for at start (Gateway API's, for Cilium);
- CRDs a bootstrap chart's *values* render objects of (Prometheus Operator's, the day one turns
  on a `ServiceMonitor`);
- secrets needed before External Secrets exists - then only its own token.

Each is applied from the file Flux reconciles, and Flux adopts it. **The fleet catalog names
them**: a component with a `bootstrap` kind (`operator`, `chart` by `order`, or `manifests`) is
installed by the bootstrap if the cluster takes it, with the CRDs of any `cilium.io` kind its
manifests hold, at Cilium's tag. The Job's ConfigMap is capped at 1 MiB, so a CRD bundle nothing
else upgrades is Talos's, a `KubeExternalManifestConfig` (Gateway API's, like Talos CCM); a
controller's own CRDs it upgrades itself stay the Job's, or `upgrade-k8s` would roll them back.
A new component that fits a rule joins the bootstrap in the same change.

**Nothing in a cluster reads NetBox.** Tofu writes what Flux needs into the `flux-runtime-info`
ConfigMap, and Flux substitutes it, strictly. The provider can't read an ASN's cluster, so the
ASN is in the data file, and must agree with NetBox's or the router drops every route.

**Flux runs a fleet, in rings.** `kubernetes/components/<name>/` is cluster-neutral: bare
manifests, a HelmRelease's values inline, no `kustomization.yaml`. What's true of a component
everywhere - its `dependsOn`, its part in the bootstrap - is in `components/fleet/catalog.yaml`.
A cluster's directory is its FluxInstance and its component list (`cluster.yaml`), joined by the
fleet ResourceSet into a Kustomization each. CI pushes `kubernetes/` as a signed OCI artifact,
`ghcr.io/iandrewt/homelab-fleet`: every main commit as `<next patch>-rc.<run>`, a `v*` tag as a
release. **A cluster syncs only its ring from Git**, `kubernetes/fleet/<name>/`: an
OCIRepository by semver range (`>=0.0.0-0` canary, `>=0.0.0` stable), verified against the
workflow's keyless signature. Promoting is tagging a release canary has run. **The bootstrap
installs exactly what the ring runs**: `homelab fleet files` resolves the ring to its artifact
and reads files at its commit with `git show`, never the working tree. Until the repo is public,
GHCR needs `GitHub Token - Flux GHCR`.

**Machine secrets and configs are never in state or on Proxmox storage.** The secrets are
generated ephemerally, written once to 1Password (`_wo`) and read back ephemerally; a node boots
the metal Secure Boot ISO into maintenance mode, with no cidata, and gets its config over the
Talos API once the guest agent answers.

**Proxmox CCM is the only cloud-node**: it writes a node's provider ID (`proxmox://<cell>/<vmid>`,
write-once) and topology - the well-known `region` is the cell, `zone` the node, where a local
volume attaches. **Both CCMs run on the host network before there is a CNI**, through KubePrism.
The Proxmox API admits what NetBox tags `proxmox-api`: the node network, and CSI controllers'
pool. A cluster asks for its Proxmox integrations in its data file (`proxmox_integrations`), and
`admin/access` makes their tokens. **The host network is only for what must run before a CNI**;
a pod needing the Proxmox API gets a pod pool of its own, on a NetBox prefix the firewall
admits. Proxmox picks VMIDs and MACs; a node is reached at the EUI-64 of the MAC in its VM's
config. IPv6 comes first wherever Kubernetes orders families.

**Pods are addressed like EKS prefix delegation**: Cilium's multi-pool IPAM hands nodes `/28`s
and `/80`s from shared pools as their pods need them, and BGP advertises the chunks. A pool is a
NetBox sub-pool of the cluster's block (`pods-default`, `pods-storage`), or a block of its own
(`pods-internet`), never wider than a `/64`; a pod joins one by `ipam.cilium.io/ip-pool`. **A
cluster has the pools NetBox gives it**: the layer passes them as JSON in runtime info, and the
`pools` component makes each pool and its advertisement. A Service joins an LB pool by its
`homelab.io/exposure` label, or gets no address. A new purpose is a NetBox role and a row in the
layer's `pools`, with its chunk size. The default pool is a plain manifest in `cilium-bgp`, for
the bootstrap. A pool's mask size is fixed once created, and at most 16 bits longer than the
pool. Nothing uses a node's `podCIDRs`.

**The API endpoint is anycast**: an `anycast` address per family in `kubernetes-api`, on every
control plane's `lo`, originated by Talos's `fabric` instance, so it routes before Cilium. Never
an L2 VIP: a `/128` on the link outranks the node's own address in source selection.

**Cilium's host firewall is the nodes' firewall** (`CiliumClusterwideNetworkPolicy`); Talos's
stays at accept, since on netfilter's input hook it drops what Cilium's proxy hands to Envoy. A
node is open from boot until Flux applies the policies.

## Host networking

Every host is **netplan → systemd-networkd, with systemd-resolved**, and every interface it has
is in netplan. Roles write netplan and assume networkd; an OS's quirks belong to its bootstrap
or its own role. Proxmox is the exception: ifupdown2, which `proxmox_node` writes from the
node's NetBox interfaces, with a static `resolv.conf`.

**A host admits what its NetBox Services do**, through nftables (`host_firewall`, one host at a
time, flushed again unless Ansible can still connect); UniFi can't see traffic within a VLAN.
A Proxmox node's firewall is tofu's.

**On a Proxmox node, Ansible owns the host OS and tofu owns Proxmox's resources**: SDN, the
firewall, storage and its ZFS pools, access and guests are tofu's; the node's interfaces,
resolvers, timezone and apt are Ansible's. Ansible never reads a cluster file - the trunk
bridge's name and MTU are stated on both sides (NetBox for the node, `sdn` in the cluster file),
and a mismatch fails in ifupdown2. SDN owns `<bridge>.<vid>` for every VNet, so a node joins a
VNet with an address on the VNet's bridge, and a host-only VLAN with a subinterface.

Raspberry Pi OS is brought into line by `bootstrap pi` before first boot: its NetworkManager
owns `/etc/netplan` (deleting every other file there) and netplan's NM backend drops
`dhcp4-overrides` and `ra-overrides`. So the bake purges NetworkManager, installs
systemd-resolved, and shadows `rpi-cloud-init-mods`' NM default with a file of the same name.

## cloud-init seeds

| Document | Holds |
|---|---|
| `meta-data` | identity: `instance-id`, `local-hostname`, `public-keys`, `region` |
| `vendor-data` | what is true of the *image* |
| `user-data` | only what is host-specific |
| `network-config` | DHCP and SLAAC; a resolver pins its own resolvers |

- The hostname comes from `local-hostname` alone; timezone is Ansible's.
- **Always write `user-data`, empty if there's nothing to say**: NoCloud requires it, and
  without it skips the whole seed silently.
- `#cloud-config` must be the first line of a cloud-config we generate. user-data is copied
  through as bytes, since what reads it may not be cloud-init (Talos's machine config).
- Raspberry Pi OS needs `users: [default]` in vendor-data, or `cc_ssh` drops the keys silently;
  `pi` already exists with `nologin`, so `runcmd` fixes its shell.

## Identity

Entra ID Free: tokens carry groups as **object IDs**, and only users can be assigned to an app.
**Groups decide**: `Administrators` and one per team (`Security` is a team), made by
`tofu/identity/entra/directory`. Each consumer looks groups up by display name at plan time and
maps the IDs back itself: Proxmox gets a `<object id>-entra` group per grant, the apiserver a
CEL map to `oidc:<team>`. **Assignments only gate**: each platform instance has its own app (its
client ID the audience), *assignment required*, and `entra/apps` assigns the members of the
groups it grants. Apps live there (`modules/entra-app`), so a rebuild keeps its client ID, and
each is consented for everyone, so no user may consent to anything. App roles are for
permissions an app defines itself, never team membership.

**Tenant policies are set by hand**: the Azure CLI can't be granted `Policy.*` scopes, so
`directory` checks what it can read of them.

AWS is ABAC: a permission set says what an entity can do, never who it is. `entra/aws` writes
the `team-<team>` SAML claims and SCIM's scope; membership is written onto users and groups as
the `aws` directory extension, which the scoping filters read, always before the scope is saved
(saving starts a sync that disables whoever is out of scope).

The organisation and its accounts are Platform's (`platform/aws/org`); what every account runs
under - guardrails, the trail, detection, the state account - is Security's (`security/aws/*`),
which reads the organisation by name; Identity Center's sets and assignments are Identity's.

The three states apply in order, by blast radius: `directory`, `aws`, `apps`, each reading the
others by name. **Nothing unattended applies them** (assignments need
`AppRoleAssignment.ReadWrite.All`); they run under an admin's `az login`. `azuread` for what it
types, `microsoft/msgraph` for the gaps - not azapi, which reaches only ARM.

## Layout

```
tools/        the `homelab` CLI: reads NetBox, renders what can't read it, syncs DNS, resolves rings, writes UniFi's firewall
ansible/      roles, playbooks, and an inventory read from NetBox
tofu/         <team>/<kind>/: a layer per kind, a workspace per instance, and shared modules
kubernetes/   components/, what clusters/<name>/ takes of them, and fleet/<name>/, its ring
unifi/        generated config for the UniFi gateways that no API takes
octodns/      octoDNS's config
netbox/       NetBox's schema
docs/         public documentation, starting with what goes in NetBox
internal/     designs, runbooks and reference - never committed (.git/info/exclude)
```

`tools/src/homelab/` is `netbox.py` (one bulk fetch, no IDs kept), `model.py` (only the joins
NetBox doesn't make - containment, a VLAN group's scope, a zone's view and policy - into plain
dicts Ansible reads as they are) and `render/` (pure functions from the model to file content:
`frr.py`, `seed.py`, and `ifupdown2.py`, which `proxmox_node` reaches through a filter plugin).

**Don't write a check that restates what's used.** Data that doesn't fit fails where it's used.
A check earns its place only where the failure would otherwise be silent, where the data is
consumed: `unzoned` in `homelab dns plan`, identity collisions in `admin/access`, a VNet joined
as a subinterface in the ifupdown2 renderer.

**State is laid out by the team that owns it**: a layer's key is its path under `tofu/`,
`<path>/terraform.tfstate` or `<path>/<workspace>/terraform.tfstate`, and the same path is its
KMS encryption context (`alias/tofu-state`). Both are in the State account, which only the
bootstrap role administers, and the bucket keeps every version under Object Lock for 90 days.
A team's role, `tofu-state-<team>`, may touch only
`${aws:PrincipalTag/team}/*`, and a layer reaches it as profile `homelab-state-<team>`, assumed
from the `homelab-state` SSO profile (`aws sso login` first). Moving a layer: change its `key`
and `encryption_context` together, then `tofu init -migrate-state`.

`tofu/platform/proxmox/clusters/schema.json` sits beside the data it describes, for editors'
`$schema` comments; CI checks the cluster files against it.

## Commands

```
uv run homelab netbox plan        # what NetBox's schema lacks; `apply`, with the Schema token, adds it
uv run homelab generate           # rewrite derived files; --check reports drift
uv run homelab dns plan           # what octoDNS would change, and a checksum
uv run homelab dns apply <sum>    # apply that plan, only if it still matches
uv run homelab pin ssh <host>     # record a host's key once it's checked, with your own token
uv run homelab unifi apply <site> <step>   # a step of the firewall, dry-run until --apply
uv run pytest

uv run homelab bootstrap pi --device /dev/sdX --hostname ratatoskr --site alfheim \
  --ssh-key-ref op://...
uv run homelab bootstrap proxmox --host yggdrasil   # its answer inside its cell's newest ISO

cd ansible && uv run ansible-playbook site.yml --limit <host>
tofu -chdir=tofu/platform/proxmox/admin/cluster plan   # workspace alf-pve-01
tofu -chdir=tofu/platform/kubernetes plan             # workspace au1-shared
uv run homelab fleet resolve au1-shared               # the artifact its ring runs
```

## Secrets

1Password is read through its SDK, never by shelling out to `op`: OpenTofu through the
1Password provider, everything else through `homelab.secrets`. Locally that's the desktop app
(`OP_ACCOUNT`), one approval per process; `OP_SERVICE_ACCOUNT_TOKEN` is for CI. Ansible's
secrets are declared in `ansible/inventory/onepassword.yml` and resolved by
`vars_plugins/onepassword.py` in Ansible's own process, since a task's worker would ask again;
`lookup('secret', ...)` is for the inventory alone.

- **Vaults are access boundaries, by team**: `Platform`, `Network`, and `Break-glass`, which only
  tools run by hand read. `admin/access` writes a tenant's Proxmox token to its team's vault,
  and the cell's own to the cluster file's `onepassword_vault`.
- **An item is titled `<Subject> <Kind> - <variable>`** in Title Case, except what's lowercase
  itself: `Proxmox Root Login - yggdrasil`, `Proxmox API Token - alf-pve-01 cluster`, `NetBox
  API Token - Ansible`. No brackets or commas: an `op://` reference takes only letters, digits,
  `-`, `_`, `.` and spaces. The personal vault is `Personal` to the SDK, whatever the app says.
- Items tofu writes are tagged `homelab/managed-by-tofu`, Password items with a `token` section
  until the provider can write an API Credential.
- A team signs in to its hosts with `SSH Key - <team>`: Platform's for root on Proxmox nodes,
  Network's for its guests and ratatoskr. No key is ever in the repo, only its item's reference.

## Conventions

**Comments are exceptional.** Explain why, never what, and only when the code can't. If you're
writing a second line of comment, ask whether the code should be clearer instead.

```
Good:  # Reboot before resize: pending changes include the old disk size
Bad:   # Update the size in the update body to match the plan size when the disk is
       # growing. Without this, the UpdateVM API call creates a pending change with...
```

**Tests earn their place or go.** Worth writing: a regression for a bug that happened, a rule
not visible from one function, a contract between modules, a protocol another tool must accept.
Not: arithmetic you can read, or that something *doesn't* appear in output. Never read live
NetBox or a real cluster file from a test; `conftest.py` builds a synthetic snapshot.

**Commits** follow Conventional Commits, signed off (`-s`), with a `Co-Authored-By: Claude ...`
trailer and never a `Claude-Session` one.

**`internal/design/` can lag the code.** Verify against it before citing one.

## Gotchas

- ansible-core 2.19+ doesn't process backslash escapes in Jinja string literals: build strings
  without them (a `|` block for a trailing newline, `select`/`reject` for a backreference).
- The `root` layer authenticates as `root@pam` with `PROXMOX_VE_PASSWORD`: nothing less can
  create `tofu@pve`.
- Proxmox's certificates are self-signed until the private CA exists, so its cluster file says
  `insecure`. Its endpoint is a machine name served by ratatoskr, so the DNS fleet's primary is
  built first.
- `ansible/roles/PowerDNS.*` and `DEBIAN13-CIS` are galaxy installs, gitignored.
- Snippets go up by SFTP as `tofu`, which has no sudo, into `/var/lib/vz/snippets`. `local` holds
  snippets alone, because every tenant has `Datastore.Allocate` on it.
