terraform {
  required_providers {
    kubernetes = {
      source = "hashicorp/kubernetes"
    }
  }
}

variable "instance" {
  type        = string
  description = "The FluxInstance."
}

variable "operator" {
  type        = string
  description = "flux-operator's release: an OCIRepository, then its HelmRelease."
}

variable "charts" {
  type = list(object({
    release = string
    adopt   = string
    values  = optional(string)
  }))
  description = "Prerequisite charts in install order, each an OCIRepository then its HelmRelease. `values` replaces the HelmRelease's; `adopt` is the DaemonSet Flux takes over."
}

variable "manifests" {
  type        = list(string)
  description = "Prerequisite manifests."
}

variable "secrets" {
  type = map(object({
    type = optional(string, "Opaque")
    data = map(string)
  }))
  description = "Secrets in flux-system, by name."
}

variable "runtime_info" {
  type        = map(string)
  description = "The data of flux-runtime-info, which Flux substitutes."
}

variable "revision" {
  type        = number
  default     = 1
  description = "Bump to run the bootstrap again."
}

locals {
  docs = {
    operator = [for d in split("\n---\n", var.operator) : yamldecode(d)]
    charts   = [for c in var.charts : [for d in split("\n---\n", c.release) : yamldecode(d)]]
  }

  operator_source = one([for d in local.docs.operator : d.spec if d.kind == "OCIRepository"])

  charts = [
    for i, c in var.charts : {
      source = one([for d in local.docs.charts[i] : d.spec if d.kind == "OCIRepository"])
      helm   = one([for d in local.docs.charts[i] : d if d.kind == "HelmRelease"])
      adopt  = c.adopt
      values = c.values
    }
  ]
}

# Its Job runs on the host network, before there is a CNI
resource "kubernetes_namespace_v1" "bootstrap" {
  metadata {
    name   = "flux-operator-bootstrap"
    labels = { "pod-security.kubernetes.io/enforce" = "privileged" }
  }
}

module "flux" {
  source  = "controlplaneio-fluxcd/flux-operator-bootstrap/kubernetes"
  version = "0.10.0"

  bootstrap_namespace        = kubernetes_namespace_v1.bootstrap.metadata[0].name
  create_bootstrap_namespace = false

  revision = var.revision

  gitops_resources = {
    instance_yaml = var.instance

    operator_chart = {
      repository  = trimprefix(local.operator_source.url, "oci://")
      version     = local.operator_source.ref.tag
      values_yaml = yamlencode(try(one([for d in local.docs.operator : d if d.kind == "HelmRelease"]).spec.values, {}))
    }

    prerequisites = {
      yamls = var.manifests
      charts = [
        for c in local.charts : {
          name                = c.helm.spec.releaseName
          repository          = trimprefix(c.source.url, "oci://")
          namespace           = try(c.helm.spec.targetNamespace, c.helm.metadata.namespace)
          create_namespace    = false
          version             = c.source.ref.tag
          values_yaml         = coalesce(c.values, yamlencode(try(c.helm.spec.values, {})))
          flux_adoption_check = { resource = "daemonset", api_group = "apps", name = c.adopt, namespace = try(c.helm.spec.targetNamespace, c.helm.metadata.namespace) }
        }
      ]
    }
  }

  managed_resources = {
    secrets_yaml = join("\n---\n", [
      for name, secret in var.secrets : yamlencode({
        apiVersion = "v1"
        kind       = "Secret"
        metadata   = { name = name }
        type       = secret.type
        stringData = secret.data
      })
    ])

    runtime_info = {
      labels = { "reconcile.fluxcd.io/watch" = "Enabled" }
      data   = var.runtime_info
    }
  }

  job = {
    host_network = true
    tolerations  = [{ operator = "Exists" }]
    env = {
      KUBERNETES_SERVICE_HOST = "localhost"
      KUBERNETES_SERVICE_PORT = "7445"
    }
  }
}
