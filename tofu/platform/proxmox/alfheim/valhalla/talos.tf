locals {
  talos = {
    cluster_name       = "valhalla"
    contract           = "v1.14" # the version the cluster started with, do not update
    kubernetes_version = "1.37.1"

    # Talos's, like Talos CCM: needed before Cilium's operator starts, and upgraded by nothing
    # else. Only upgrade-k8s re-applies it, and dropping it would prune every Gateway with it.
    gateway_api = "v1.6.1"

    oidc_client_id = "f0c7bdb5-af0e-44e8-a1c7-abde3b6f7a7c"

    # Every control plane originates it from `lo`, so the router spreads clients across them
    api      = { for family, a in module.netbox.anycast["kubernetes-api"] : family => one(a) }
    endpoint = "https://${one(distinct(flatten(values(module.netbox.anycast["kubernetes-api"]))[*].dns_name))}:6443"
  }

  family_order = ["ipv6", "ipv4"]

  # Node-internal: one per node, identical on all of them, never routed anywhere
  workload = {
    asn          = 4200000002
    cilium_asn   = 4200000003
    host_veth    = "veth-workload"
    host_address = "fd1d:3fe0:7c97::"
    vrf_address  = "fd1d:3fe0:7c97::1"
  }
  fabric_asn = 64514

  management = flatten([for name in local.cluster.firewall.management_vlans : module.netbox.vlans[name].subnets[*].cidr])
  k8s        = [for f in local.family_order : local.subnets.k8s[f].cidr]
  pods       = [for f in local.family_order : module.netbox.prefixes["kubernetes-pods"][f]]

  lb_pools = ["lb-local", "lb-shared", "lb-external"]
}

data "helm_template" "talos_ccm" {
  name         = "talos-cloud-controller-manager"
  namespace    = "kube-system"
  repository   = "oci://ghcr.io/siderolabs/charts"
  chart        = "talos-cloud-controller-manager"
  version      = "0.5.8"
  kube_version = local.talos.kubernetes_version

  values = [yamlencode({
    enabledControllers = ["node-csr-approval"]
    daemonSet = {
      enabled = true
      k8s     = { serviceHost = "localhost", servicePort = 7445 }
    }
  })]
}

resource "talos_image_factory_schematic" "class" {
  for_each = local.classes

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
  talos_version = local.talos.contract
}

resource "onepassword_item" "secrets" {
  vault    = data.onepassword_vault.tenant.uuid
  title    = "Talos Secrets - ${local.talos.cluster_name}"
  category = "secure_note"
  tags     = ["homelab/managed-by-tofu"]

  note_value_wo         = jsonencode(ephemeral.talos_machine_secrets.generated.machine_secrets)
  note_value_wo_version = 1
}

ephemeral "onepassword_item" "secrets" {
  vault = data.onepassword_vault.tenant.uuid
  uuid  = onepassword_item.secrets.uuid
}

ephemeral "talos_client_configuration" "this" {
  cluster_name    = local.talos.cluster_name
  machine_secrets = local.machine_secrets
  endpoints       = local.controlplane
  nodes           = values(local.address)
}

locals {
  machine_secrets = jsondecode(ephemeral.onepassword_item.secrets.note_value)
}

locals {
  installer = {
    for class, c in local.classes :
    class => "factory.talos.dev/metal-installer-secureboot/${talos_image_factory_schematic.class[class].id}:${c.talos}"
  }

  docs = {
    all = [
      { apiVersion = "v1alpha1", kind = "KubeFlannelCNIConfig", "$patch" = "delete" },
      { apiVersion = "v1alpha1", kind = "HostnameConfig", "$patch" = "delete" },
      {
        apiVersion     = "v1alpha1"
        kind           = "KubeNetworkConfig"
        podSubnets     = local.pods
        serviceSubnets = ["fd00:10:96::/112", "10.96.0.0/12"]
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
        nodeIP     = { validSubnets = local.k8s }
      },
      { apiVersion = "v1alpha1", kind = "DHCPv4Config", name = local.links.k8s },
      { apiVersion = "v1alpha1", kind = "DHCPv4Config", name = local.links.storage, ignoreRoutes = true },
      {
        apiVersion = "v1alpha1"
        kind       = "SysctlConfig"
        params     = { "net/ipv6/conf/${local.links.storage}/accept_ra_defrtr" = "0" }
      },
      # Pods reach `storage` through the router, so its replies come back the way they left
      {
        apiVersion = "v1alpha1"
        kind       = "LinkConfig"
        name       = local.links.k8s
        routes = [
          for f in local.family_order : {
            destination = local.subnets.storage[f].cidr
            gateway     = local.subnets.k8s[f].gateway
            table       = "100"
          }
        ]
      },
      [
        for i, prefix in local.pods : {
          apiVersion = "v1alpha1"
          kind       = "RoutingRuleConfig"
          name       = tostring(1000 + i)
          src        = prefix
          table      = "100"
          action     = "unicast"
        }
      ],
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
      [
        for name in ["STATE", "EPHEMERAL"] : {
          apiVersion = "v1alpha1"
          kind       = "VolumeConfig"
          name       = name
          encryption = {
            provider = "luks2"
            keys = [merge(
              { slot = 0, tpm = { checkSecurebootStatusOnEnroll = true } },
              name == "STATE" ? {} : { lockToState = true },
            )]
          }
        }
      ],
    ]

    controlplane = [
      { cluster = { etcd = { advertisedSubnets = [local.subnets.k8s.ipv6.cidr] } } },
      { apiVersion = "v1alpha1", kind = "KubeProxyConfig", enabled = false },
      {
        apiVersion = "v1alpha1"
        kind       = "KubeExternalManifestConfig"
        name       = "gateway-api-crds"
        url        = "https://github.com/kubernetes-sigs/gateway-api/releases/download/${local.talos.gateway_api}/standard-install.yaml"
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
          for f in local.family_order : { address = "${local.talos.api[f].address}/${f == "ipv6" ? 128 : 32}" }
        ]
      },
      {
        apiVersion    = "v1alpha1"
        kind          = "KubeAPIServerConfig"
        certExtraSANs = [for f in local.family_order : local.talos.api[f].address]
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
              url       = local.cluster.oidc.issuer_url
              audiences = [local.talos.oidc_client_id]
            }
            claimMappings = {
              username = { claim = "preferred_username", prefix = "oidc:" }
              groups   = { claim = "roles", prefix = "oidc:" }
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
    for name, n in local.nodes : name => [
      { apiVersion = "v1alpha1", kind = "HostnameConfig", hostname = name },
      {
        apiVersion   = "v1alpha1"
        kind         = "UnattendedInstallConfig"
        installer    = { image = local.installer[n.class] }
        provisioning = { diskSelector = { match = "disk.dev_path == \"/dev/sda\"" } }
      },
      {
        apiVersion = "v1alpha1"
        kind       = "KubeNodeConfig"
        labels     = try(local.classes[n.class].labels, {})
      },
      {
        apiVersion = "v1alpha1"
        kind       = "BGPInstanceConfig"
        name       = "workload"
        vrf        = "vrf-workload"
        localASN   = local.workload.asn
        # Only Cilium, on this node, ever sees it, and Cilium takes the node's IPv4
        routerID      = "0.0.0.1"
        installRoutes = false
        neighbors = [{
          address = local.workload.host_address
          peerASN = local.workload.cilium_asn
          passive = true
        }]
      },
      {
        apiVersion = "v1alpha1"
        kind       = "BGPInstanceConfig"
        name       = "fabric"
        localASN   = local.fabric_asn
        # The router ID is the first IPv4 address advertised: the node's lease. The router drops
        # the node's own addresses, as it does anything outside a purpose's block.
        advertise = concat([local.links.k8s], local.classes[n.class].machine_type == "controlplane" ? ["lo"] : [])
        importRoutes = [{
          bgpInstance = "workload"
          prefixes = concat(local.pods, flatten([
            for pool in local.lb_pools : [for f in local.family_order : module.netbox.prefixes[pool][f]]
          ]))
        }]
        # A session per family: over IPv4, GoBGP's IPv6 next hop is IPv4-mapped, which the
        # router can't install - its IPv4 group carries IPv6 only for speakers that can
        neighbors = [for f in local.family_order : { address = local.subnets.k8s[f].gateway, peerASN = 0 }]
      },
    ]
  }
}

ephemeral "talos_machine_configuration" "node" {
  for_each = local.nodes

  cluster_name       = local.talos.cluster_name
  cluster_endpoint   = local.talos.endpoint
  machine_type       = local.classes[each.value.class].machine_type
  machine_secrets    = local.machine_secrets
  talos_version      = local.talos.contract
  kubernetes_version = local.talos.kubernetes_version
  docs               = false
  examples           = false

  config_patches = concat(
    [local.patches.all],
    local.classes[each.value.class].machine_type == "controlplane" ? [local.patches.controlplane] : [],
    [local.patches.node[each.key]],
  )
}

locals {
  patches = {
    all          = join("\n---\n", [for d in flatten(local.docs.all) : yamlencode(d)])
    controlplane = join("\n---\n", [for d in flatten(local.docs.controlplane) : yamlencode(d)])
    node         = { for name, docs in local.node_docs : name => join("\n---\n", [for d in docs : yamlencode(d)]) }
  }
}

# Rolls a version bump node by node only when applied with -parallelism=1
resource "talos_machine" "node" {
  for_each = local.nodes

  node                     = local.address[each.key]
  client_configuration_wo  = ephemeral.talos_client_configuration.this.client_configuration
  machine_configuration_wo = ephemeral.talos_machine_configuration.node[each.key].machine_configuration
  image                    = local.installer[each.value.class]

  ignore_kubernetes_upgrade_drift = true
  drain_on_upgrade                = true
  kubeconfig_wo                   = ephemeral.talos_cluster_kubeconfig.admin.kubeconfig_raw

  depends_on = [module.node]
}

resource "talos_cluster" "this" {
  node                    = local.controlplane[0]
  control_plane_nodes     = local.controlplane
  client_configuration_wo = ephemeral.talos_client_configuration.this.client_configuration
  kubernetes_version      = local.talos.kubernetes_version

  depends_on = [talos_machine.node]
}

# Bump the version to mint a new client certificate
resource "onepassword_item" "talosconfig" {
  vault    = data.onepassword_vault.tenant.uuid
  title    = "Talos Config - ${local.talos.cluster_name}"
  category = "secure_note"
  tags     = ["homelab/managed-by-tofu"]

  note_value_wo         = ephemeral.talos_client_configuration.this.talos_config
  note_value_wo_version = 1
}
