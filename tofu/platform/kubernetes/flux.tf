# The fleet's files at the commit the cluster's ring runs, never the working tree
data "external" "fleet" {
  program     = ["uv", "run", "--quiet", "homelab", "fleet", "files"]
  working_dir = path.module
  query       = { cluster = local.name }
}

locals {
  fleet = data.external.fleet.result

  # What the cluster takes, from the catalog; the bootstrap installs what has a `bootstrap` kind
  catalog = yamldecode(local.fleet["components/fleet/catalog.yaml"]).spec.defaultValues.components
  taken   = yamldecode(local.fleet["clusters/${local.name}/cluster.yaml"]).spec.defaultValues.components
  bootstrap = {
    operator = [for name in local.taken : name if try(local.catalog[name].bootstrap.kind, "") == "operator"]
    chart = [
      for c in sort([for name in local.taken : format("%03d %s", local.catalog[name].bootstrap.order, name) if try(local.catalog[name].bootstrap.kind, "") == "chart"]) :
      split(" ", c)[1]
    ]
    manifests = [for name in local.taken : name if try(local.catalog[name].bootstrap.kind, "") == "manifests"]
  }
  component_files = {
    for name in local.taken : name => [for path, body in local.fleet : body if startswith(path, "components/${name}/")]
  }
  manifests = flatten([for name in local.bootstrap.manifests : local.component_files[name]])

  # Cilium registers its CRDs only once it runs, after the manifests that need them
  cilium     = one([for d in split("\n---\n", local.fleet["components/cilium/release.yaml"]) : yamldecode(d).spec.ref.tag if yamldecode(d).kind == "OCIRepository"])
  cilium_crd = toset([for d in flatten([for m in local.manifests : split("\n---\n", m)]) : "${split("/", yamldecode(d).apiVersion)[1]}/${lower(yamldecode(d).kind)}s" if startswith(yamldecode(d).apiVersion, "cilium.io/")])

  # Merged over the chart's values, by Flux and the bootstrap alike
  proxmox_ccm = {
    config = {
      features = { provider = "default" }
      clusters = [
        for pve, token in data.onepassword_item.proxmox_ccm : {
          url = "${trimsuffix(local.proxmox[pve].endpoint, "/")}/api2/json"
          # The item holds the whole token, `user@realm!name=secret`, as the providers take it
          token_id     = split("=", token.password)[0]
          token_secret = split("=", token.password)[1]
          region       = pve
        }
      ]
    }
  }

  # The default pool is the bootstrap's, so a manifest of its own; the rest are the pools
  # component's inputs. Compact JSON has no spaces, which Flux's substitution needs.
  pool_info = merge(
    { for f, prefix in local.owned["pods-default"] : "pods_default_${f}" => prefix },
    {
      pools = jsonencode([
        for purpose, pool in local.pools : merge(
          { for k, v in pool : k => v if k != "role" },
          local.owned[purpose],
        ) if purpose != "pods-default" && length(local.owned[purpose]) > 0
      ])
    },
  )
}

data "onepassword_item" "proxmox_ccm" {
  for_each = contains(local.taken, "proxmox-ccm") ? local.proxmox : {}

  vault = data.onepassword_vault.team.uuid
  title = "Proxmox API Token - ${each.key} tenant-${local.team}-${local.name}-ccm"
}

data "onepassword_item" "flux_github_app" {
  vault = data.onepassword_vault.team.uuid
  title = "GitHub App Key - Flux ${local.name}"
}

# While the repository is private, GHCR needs a token to pull the fleet
data "onepassword_item" "ghcr" {
  vault = data.onepassword_vault.team.uuid
  title = "GitHub Token - Flux GHCR"
}

data "http" "cilium_crd" {
  for_each = local.cilium_crd

  url = "https://raw.githubusercontent.com/cilium/cilium/v${local.cilium}/pkg/k8s/apis/cilium.io/client/crds/${each.key}.yaml"
}

module "fleet" {
  source = "../../modules/fleet-bootstrap"

  instance = local.fleet["clusters/${local.name}/flux-instance.yaml"]
  operator = one([for name in local.bootstrap.operator : one(local.component_files[name])])
  charts = [
    for name in local.bootstrap.chart : {
      release = one(local.component_files[name])
      adopt   = local.catalog[name].bootstrap.adopt
      values = name == "proxmox-ccm" ? yamlencode(merge(
        yamldecode(one([for d in split("\n---\n", one(local.component_files[name])) : d if yamldecode(d).kind == "HelmRelease"])).spec.values,
        local.proxmox_ccm,
      )) : null
    }
  ]
  manifests = concat([for crd in data.http.cilium_crd : crd.response_body], local.manifests)

  secrets = merge(contains(local.taken, "proxmox-ccm") ? {
    proxmox-ccm-config = { data = { "values.yaml" = yamlencode(local.proxmox_ccm) } }
    } : {}, {
    flux-github-app = {
      data = {
        githubAppID                = data.onepassword_item.flux_github_app.username
        githubAppInstallationOwner = "iandrewt"
        githubAppPrivateKey        = data.onepassword_item.flux_github_app.credential
      }
    }
    ghcr-pull = {
      type = "kubernetes.io/dockerconfigjson"
      data = {
        ".dockerconfigjson" = jsonencode({
          auths = {
            "ghcr.io" = {
              username = data.onepassword_item.ghcr.username
              password = data.onepassword_item.ghcr.credential
              auth     = base64encode("${data.onepassword_item.ghcr.username}:${data.onepassword_item.ghcr.credential}")
            }
          }
        })
      }
    }
  })

  runtime_info = merge(local.pool_info, {
    cluster_name           = local.name
    cluster_id             = tostring(local.cluster.number)
    api_sources_cidrs      = "[${join(",", concat(module.netbox.audiences["kubernetes-api"].ipv6, module.netbox.audiences["kubernetes-api"].ipv4))}]"
    dhcp_servers_cidrs     = "[${local.subnets[local.node_vlan.vnet].ipv4.gateway}/32]"
    cilium_asn             = tostring(local.asn.cilium)
    cilium_veth            = module.talos.workload.veth
    talos_workload_asn     = tostring(local.asn.workload)
    talos_workload_address = module.talos.workload.address
  })

  depends_on = [module.talos]
}

output "fleet" {
  value = { version = local.fleet.version, revision = local.fleet.revision }
}
