variable "name" {
  type        = string
  description = "`k8s-<number>`, org-unique."
}

variable "contract" {
  type        = string
  description = "The Talos version the cluster started with, never updated."
}

variable "kubernetes_version" {
  type = string
}

variable "gateway_api" {
  type        = string
  description = "Gateway API's release, whose CRDs Talos applies: Cilium's operator needs them before it starts."
}

variable "api" {
  type = object({
    endpoint  = string
    addresses = map(string)
  })
  description = "The API endpoint, and its anycast address per family."
}

variable "classes" {
  type = map(object({
    machine_type = string
    lifecycle    = string
    talos        = string
    extensions   = list(string)
    labels       = optional(map(string), {})
  }))
  description = "Node classes. A `transient` node is tainted, and its volumes are keyed to the node rather than sealed to a TPM."

  validation {
    condition     = alltrue([for c in var.classes : contains(["permanent", "transient"], c.lifecycle)])
    error_message = "A class's lifecycle is permanent or transient."
  }
}

variable "nodes" {
  type = map(object({
    class        = string
    address      = string
    install_disk = string
    links        = object({ node = string, san = optional(string) })
    san_address  = optional(string)
  }))
  description = "Every node, VM or metal: its class, the IPv6 address its Talos API answers on, its install disk as CEL, and its links."
}

variable "network" {
  type = object({
    node     = map(object({ cidr = string, gateway = string }))
    san      = optional(string)
    pods     = map(string)
    services = optional(map(string), { ipv6 = "fd00:10:96::/112", ipv4 = "10.96.0.0/12" })
  })
  description = "Per family: the node network and its gateways, the pod block, and service CIDRs."
}

variable "bgp" {
  type = object({
    asn          = number
    workload_asn = number
    cilium_asn   = number
    import       = list(string)
  })
  description = "The cluster's ASN, the node-internal ones, and every block the cluster may advertise."
}

variable "oidc" {
  type = object({
    issuer    = string
    client_id = string
    groups    = map(string)
  })
  description = "Entra's issuer, the cluster's app, and each granted group's object ID to its name in RBAC."
}

variable "onepassword_vault" {
  type        = string
  description = "The vault the machine secrets and talosconfig go to."
}

variable "talos_ccm_version" {
  type    = string
  default = "0.5.8"
}
