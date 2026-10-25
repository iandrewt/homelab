locals {
  family_order = ["ipv6", "ipv4"]

  # Node-internal, and the same on every node
  workload = {
    host_veth    = "veth-workload"
    host_address = "fd1d:3fe0:7c97::"
    vrf_address  = "fd1d:3fe0:7c97::1"
  }

  controlplane = [for name, n in var.nodes : n.address if var.classes[n.class].machine_type == "controlplane"]
}

data "helm_template" "talos_ccm" {
  name         = "talos-cloud-controller-manager"
  namespace    = "kube-system"
  repository   = "oci://ghcr.io/siderolabs/charts"
  chart        = "talos-cloud-controller-manager"
  version      = var.talos_ccm_version
  kube_version = var.kubernetes_version

  values = [yamlencode({
    enabledControllers = ["node-csr-approval"]
    daemonSet = {
      enabled = true
      k8s     = { serviceHost = "localhost", servicePort = 7445 }
    }
  })]
}

resource "talos_image_factory_schematic" "class" {
  for_each = var.classes

  schematic = yamlencode({
    customization = {
      extraKernelArgs = ["lockdown=integrity"] # default in v1.15, need it early for cilium
      systemExtensions = {
        officialExtensions = each.value.extensions
      }
    }
  })
}

ephemeral "talos_machine_secrets" "generated" {
  talos_version = var.contract
}

resource "onepassword_item" "secrets" {
  vault    = var.onepassword_vault
  title    = "Talos Secrets - ${var.name}"
  category = "secure_note"
  tags     = ["homelab/managed-by-tofu"]

  note_value_wo         = jsonencode(ephemeral.talos_machine_secrets.generated.machine_secrets)
  note_value_wo_version = 1
}

ephemeral "onepassword_item" "secrets" {
  vault = var.onepassword_vault
  uuid  = onepassword_item.secrets.uuid
}

locals {
  machine_secrets = jsondecode(ephemeral.onepassword_item.secrets.note_value)
}

ephemeral "talos_client_configuration" "this" {
  cluster_name    = var.name
  machine_secrets = local.machine_secrets
  endpoints       = local.controlplane
  nodes           = [for n in var.nodes : n.address]
}

# Minted from the machine secrets, so nothing needs the cluster to exist at plan
ephemeral "talos_cluster_kubeconfig" "admin" {
  machine_secrets = local.machine_secrets
  cluster_name    = var.name
  endpoint        = var.api.endpoint
}

locals {
  installer = {
    for class, c in var.classes :
    class => "factory.talos.dev/metal-installer-secureboot/${talos_image_factory_schematic.class[class].id}:${c.talos}"
  }

  # A transient node may have no TPM
  volume_keys = {
    permanent = { STATE = { tpm = { checkSecurebootStatusOnEnroll = true } }, EPHEMERAL = { tpm = { checkSecurebootStatusOnEnroll = true }, lockToState = true } }
    transient = { STATE = { nodeID = {} }, EPHEMERAL = { nodeID = {} } }
  }

  docs = {
    all = [
      { apiVersion = "v1alpha1", kind = "KubeFlannelCNIConfig", "$patch" = "delete" },
      { apiVersion = "v1alpha1", kind = "HostnameConfig", "$patch" = "delete" },
      {
        apiVersion     = "v1alpha1"
        kind           = "KubeNetworkConfig"
        podSubnets     = [for f in local.family_order : var.network.pods[f]]
        serviceSubnets = [for f in local.family_order : var.network.services[f]]
      },
      {
        apiVersion = "v1alpha1"
        kind       = "KubeletConfig"
        config     = { serverTLSBootstrap = true }
        extraArgs  = { cloud-provider = "external" }
      },
      {
        apiVersion = "v1alpha1"
        kind       = "KubeNodeConfig"
        nodeIP     = { validSubnets = [for f in local.family_order : var.network.node[f].cidr] }
      },
      {
        apiVersion = "v1alpha1"
        kind       = "VethConfig"
        name       = local.workload.host_veth
        addresses  = [{ address = "${local.workload.host_address}/127" }]
        peer = {
          name      = "veth-router"
          addresses = [{ address = "${local.workload.vrf_address}/127" }]
        }
      },
      { apiVersion = "v1alpha1", kind = "VRFConfig", name = "vrf-workload", links = ["veth-router"], table = "88" },
    ]

    controlplane = [
      { cluster = { etcd = { advertisedSubnets = [var.network.node.ipv6.cidr] } } },
      { apiVersion = "v1alpha1", kind = "KubeProxyConfig", enabled = false },
      {
        apiVersion = "v1alpha1"
        kind       = "KubeExternalManifestConfig"
        name       = "gateway-api-crds"
        url        = "https://github.com/kubernetes-sigs/gateway-api/releases/download/${var.gateway_api}/standard-install.yaml"
      },
      {
        apiVersion = "v1alpha1"
        kind       = "KubeControllerManagerConfig"
        extraArgs  = { allocate-node-cidrs = "false" } # using cilium multi-pool instead
      },
      # Control planes run workloads, and advertise LoadBalancer addresses like any other node
      {
        apiVersion = "v1alpha1"
        kind       = "KubeNodeConfig"
        labels     = { "node.kubernetes.io/exclude-from-external-load-balancers" = { "$patch" = "delete" } }
        taints     = { "node-role.kubernetes.io/control-plane" = { "$patch" = "delete" } }
      },
      {
        apiVersion = "v1alpha1"
        kind       = "LinkConfig"
        name       = "lo"
        addresses = [
          for f in local.family_order : { address = "${var.api.addresses[f]}/${f == "ipv6" ? 128 : 32}" }
        ]
      },
      {
        apiVersion    = "v1alpha1"
        kind          = "KubeAPIServerConfig"
        certExtraSANs = [for f in local.family_order : var.api.addresses[f]]
      },
      {
        apiVersion = "v1alpha1"
        kind       = "KubeAuthenticationConfig"
        configuration = {
          anonymous = {
            enabled    = true
            conditions = [for p in ["/livez", "/readyz", "/healthz"] : { path = p }]
          }
          jwt = [{
            issuer = {
              url       = var.oidc.issuer
              audiences = [var.oidc.client_id]
            }
            claimMappings = {
              username = { claim = "preferred_username", prefix = "oidc:" }
              # Entra Free sends object IDs. A JSON object is a CEL map.
              groups = {
                expression = "has(claims.groups) ? claims.groups.filter(g, g in ${jsonencode(var.oidc.groups)}).map(g, 'oidc:' + ${jsonencode(var.oidc.groups)}[g]) : []"
              }
            }
          }]
        }
      },
      {
        apiVersion = "v1alpha1"
        kind       = "KubeInlineManifestConfig"
        name       = "talos-cloud-controller-manager"
        manifest   = data.helm_template.talos_ccm.manifest
      },
      {
        apiVersion                  = "v1alpha1"
        kind                        = "KubeTalosAPIAccessConfig"
        allowedRoles                = ["os:reader"]
        allowedKubernetesNamespaces = ["kube-system"]
      },
    ]
  }

  node_docs = {
    for name, n in var.nodes : name => concat(
      [
        { apiVersion = "v1alpha1", kind = "HostnameConfig", hostname = name },
        {
          apiVersion   = "v1alpha1"
          kind         = "UnattendedInstallConfig"
          installer    = { image = local.installer[n.class] }
          provisioning = { diskSelector = { match = n.install_disk } }
        },
        {
          apiVersion = "v1alpha1"
          kind       = "KubeNodeConfig"
          labels     = merge(var.classes[n.class].labels, { "node.homelab.io/lifecycle" = var.classes[n.class].lifecycle })
          taints     = var.classes[n.class].lifecycle == "transient" ? { "node.homelab.io/lifecycle" = "transient:NoSchedule" } : {}
        },
        { apiVersion = "v1alpha1", kind = "DHCPv4Config", name = n.links.node },
        {
          apiVersion = "v1alpha1"
          kind       = "BGPInstanceConfig"
          name       = "workload"
          vrf        = "vrf-workload"
          localASN   = var.bgp.workload_asn
          # Only Cilium, on this node, ever sees it, and Cilium takes the node's IPv4
          routerID      = "0.0.0.1"
          installRoutes = false
          neighbors = [{
            address = local.workload.host_address
            peerASN = var.bgp.cilium_asn
            passive = true
          }]
        },
        {
          apiVersion = "v1alpha1"
          kind       = "BGPInstanceConfig"
          name       = "fabric"
          localASN   = var.bgp.asn
          # The router ID is the first IPv4 address advertised: the node's lease
          advertise    = concat([n.links.node], var.classes[n.class].machine_type == "controlplane" ? ["lo"] : [])
          importRoutes = [{ bgpInstance = "workload", prefixes = var.bgp.import }]
          # A session per family: over IPv4, GoBGP's IPv6 next hop is IPv4-mapped
          neighbors = [for f in local.family_order : { address = var.network.node[f].gateway, peerASN = 0 }]
        },
      ],
      [
        for volume, key in local.volume_keys[var.classes[n.class].lifecycle] : {
          apiVersion = "v1alpha1"
          kind       = "VolumeConfig"
          name       = volume
          encryption = { provider = "luks2", keys = [merge({ slot = 0 }, key)] }
        }
      ],
      # Nothing answers DHCP or RA on a VLAN-only network
      n.links.san == null ? [] : [{
        apiVersion = "v1alpha1"
        kind       = "LinkConfig"
        name       = n.links.san
        addresses  = [{ address = "${n.san_address}/64" }]
      }],
    )
  }

  patches = {
    all          = join("\n---\n", [for d in local.docs.all : yamlencode(d)])
    controlplane = join("\n---\n", [for d in local.docs.controlplane : yamlencode(d)])
    node         = { for name, docs in local.node_docs : name => join("\n---\n", [for d in docs : yamlencode(d)]) }
  }
}

ephemeral "talos_machine_configuration" "node" {
  for_each = var.nodes

  cluster_name       = var.name
  cluster_endpoint   = var.api.endpoint
  machine_type       = var.classes[each.value.class].machine_type
  machine_secrets    = local.machine_secrets
  talos_version      = var.contract
  kubernetes_version = var.kubernetes_version
  docs               = false
  examples           = false

  config_patches = concat(
    [local.patches.all],
    var.classes[each.value.class].machine_type == "controlplane" ? [local.patches.controlplane] : [],
    [local.patches.node[each.key]],
  )
}

# Rolls a version bump node by node only when applied with -parallelism=1
resource "talos_machine" "node" {
  for_each = var.nodes

  node                     = each.value.address
  client_configuration_wo  = ephemeral.talos_client_configuration.this.client_configuration
  machine_configuration_wo = ephemeral.talos_machine_configuration.node[each.key].machine_configuration
  image                    = local.installer[each.value.class]

  ignore_kubernetes_upgrade_drift = true
  drain_on_upgrade                = true
  kubeconfig_wo                   = ephemeral.talos_cluster_kubeconfig.admin.kubeconfig_raw
}

resource "talos_cluster" "this" {
  node                    = local.controlplane[0]
  control_plane_nodes     = local.controlplane
  client_configuration_wo = ephemeral.talos_client_configuration.this.client_configuration
  kubernetes_version      = var.kubernetes_version

  depends_on = [talos_machine.node]
}

# Bump the version to mint a new client certificate
resource "onepassword_item" "talosconfig" {
  vault    = var.onepassword_vault
  title    = "Talos Config - ${var.name}"
  category = "secure_note"
  tags     = ["homelab/managed-by-tofu"]

  note_value_wo         = ephemeral.talos_client_configuration.this.talos_config
  note_value_wo_version = 1
}
