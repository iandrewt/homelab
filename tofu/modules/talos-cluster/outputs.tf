output "kubernetes" {
  description = "An admin client, minted per run."
  value       = ephemeral.talos_cluster_kubeconfig.admin.kubernetes_client_configuration
  ephemeral   = true
}

output "ready" {
  description = "Known once Talos is healthy across the control planes."
  value       = talos_cluster.this.id
}

output "workload" {
  description = "The node-internal end of Talos's `workload` instance, which Cilium peers with."
  value       = { veth = local.workload.host_veth, address = local.workload.vrf_address }
}
