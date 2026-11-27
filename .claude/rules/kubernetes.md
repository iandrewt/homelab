---
paths:
  - "kubernetes/**"
  - "tofu/platform/kubernetes/**"
  - "tofu/modules/talos-cluster/**"
  - "tofu/modules/fleet-bootstrap/**"
  - "tools/src/homelab/fleet.py"
---

# Kubernetes

A cluster is Talos in one region, across its Proxmox cells and its metal, named
`<region>-k8s-<purpose>` (`au1-k8s-shared`). One layer builds every one, `tofu/platform/kubernetes`, a
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
`ghcr.io/iandrewt/homelab-fleet`: every main commit as `<next patch>-rc.<run>`, a `fleet/v*` tag as a
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
