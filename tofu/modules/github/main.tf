# What a role trusting GitHub's issuer names: by GitHub's immutable IDs, never names

output "repository" {
  description = "The repository, as its immutable subject claim names it, the owner and repository by ID too."
  value       = "iandrewt@12795256/homelab@1394808425"
}

output "workflow" {
  description = "The one workflow file that may plan or apply, on main."
  value       = "iandrewt/homelab/.github/workflows/tofu.yml@refs/heads/main"
}

output "operator" {
  description = "The one person whose runs may apply: the repository's owner."
  value       = "12795256"
}

output "planners" {
  description = "Whose runs may plan: the operator, and Dependabot's, so a provider bump is planned."
  value       = ["12795256", "49699333"]
}
