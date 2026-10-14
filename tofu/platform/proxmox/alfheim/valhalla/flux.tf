locals {
  flux = "${path.module}/../../../../../kubernetes/clusters/${local.talos.cluster_name}"

  # What Flux reconciles is what the bootstrap installs: the same chart, version and values
  charts = {
    for name, path in {
      flux-operator                    = "flux-system/flux-operator.yaml"
      cilium                           = "infrastructure/controllers/cilium/release.yaml"
      proxmox-cloud-controller-manager = "infrastructure/controllers/proxmox-ccm/release.yaml"
    } : name => yamldecode(split("\n---\n", file("${local.flux}/${path}"))[0]).spec
  }

  instance = yamldecode(file("${local.flux}/flux-system/flux-instance.yaml"))

  # Proxmox CCM's whole `config`: Flux merges it over the chart values in Git, and the bootstrap
  # installs the same merge
  proxmox_ccm = {
    config = {
      features = { provider = "default" }
      clusters = [{
        url          = "${trimsuffix(local.cluster.endpoint, "/")}/api2/json"
        token_id     = data.onepassword_item.proxmox_ccm.section_map["token"].field_map["token id"].value
        token_secret = data.onepassword_item.proxmox_ccm.password
        region       = local.cluster_name
      }]
    }
  }
}

data "onepassword_item" "proxmox_ccm" {
  vault = data.onepassword_vault.tenant.uuid
  title = "Proxmox API Token - ${local.cluster_name} tenant-${local.tenant}-ccm"
}

data "onepassword_item" "flux_github_app" {
  vault = data.onepassword_vault.tenant.uuid
  title = "GitHub App Key - Flux ${local.talos.cluster_name}"
}

module "flux" {
  source  = "controlplaneio-fluxcd/flux-operator-bootstrap/kubernetes"
  version = "0.9.0"

  revision = 1

  gitops_resources = {
    instance_yaml = file("${local.flux}/flux-system/flux-instance.yaml")

    operator_chart = {
      repository  = trimprefix(local.charts.flux-operator.url, "oci://")
      version     = local.charts.flux-operator.ref.tag
      values_yaml = file("${local.flux}/flux-system/flux-operator-values.yaml")
    }

    # In this order: nothing schedules until Cilium runs, then Proxmox CCM lifts the taint an
    # external cloud provider puts on every new node
    prerequisites = {
      charts = [
        {
          name                = "cilium"
          repository          = trimprefix(local.charts.cilium.url, "oci://")
          namespace           = "kube-system"
          create_namespace    = false
          version             = local.charts.cilium.ref.tag
          values_yaml         = file("${local.flux}/infrastructure/controllers/cilium/values.yaml")
          flux_adoption_check = { resource = "daemonset", api_group = "apps", name = "cilium", namespace = "kube-system" }
        },
        {
          name                = "proxmox-cloud-controller-manager"
          repository          = trimprefix(local.charts.proxmox-cloud-controller-manager.url, "oci://")
          namespace           = "kube-system"
          create_namespace    = false
          version             = local.charts.proxmox-cloud-controller-manager.ref.tag
          values_yaml         = yamlencode(merge(yamldecode(file("${local.flux}/infrastructure/controllers/proxmox-ccm/values.yaml")), local.proxmox_ccm))
          flux_adoption_check = { resource = "deployment", api_group = "apps", name = "proxmox-cloud-controller-manager", namespace = "kube-system" }
        },
      ]
    }
  }

  managed_resources = {
    secrets_yaml = join("\n---\n", [
      yamlencode({
        apiVersion = "v1"
        kind       = "Secret"
        metadata   = { name = local.instance.spec.sync.pullSecret }
        type       = "Opaque"
        stringData = {
          githubAppClientID          = data.onepassword_item.flux_github_app.username
          githubAppInstallationOwner = regex("github\\.com/([^/]+)/", local.instance.spec.sync.url)[0]
          githubAppPrivateKey        = data.onepassword_item.flux_github_app.credential
        }
      }),
      yamlencode({
        apiVersion = "v1"
        kind       = "Secret"
        metadata   = { name = "proxmox-ccm-config" }
        type       = "Opaque"
        stringData = { "values.yaml" = yamlencode(local.proxmox_ccm) }
      }),
    ])

    # NetBox's facts, for Flux to substitute: nothing in the cluster reads NetBox itself
    runtime_info = {
      labels = { "reconcile.fluxcd.io/watch" = "Enabled" }
      data = merge(
        merge([
          for pool in local.lb_pools : {
            for f in local.family_order : "${replace(pool, "-", "_")}_${f}" => module.netbox.prefixes[pool][f]
          }
        ]...),
        { for f in local.family_order : "pods_${f}" => module.netbox.prefixes["kubernetes-pods"][f] },
        {
          cilium_asn             = tostring(local.workload.cilium_asn)
          cilium_veth            = local.workload.host_veth
          talos_workload_asn     = tostring(local.workload.asn)
          talos_workload_address = local.workload.vrf_address
        },
      )
    }
  }

  job = {
    host_network = true
    tolerations  = [{ operator = "Exists" }]
  }

  # The Talos layer is healthy across the control planes once this exists
  depends_on = [talos_cluster.this]
}
