locals {
  dir   = "${path.module}/../../identity/tenancy/teams"
  teams = { for f in fileset(local.dir, "*.yaml") : trimsuffix(f, ".yaml") => yamldecode(file("${local.dir}/${f}")) }
}

output "teams" {
  description = "Every team in the catalog, by name."
  value       = local.teams

  # Copied out of the repo, it would find no teams, and plan every team's resources away
  precondition {
    condition     = length(local.teams) > 0
    error_message = "No teams found in ${local.dir}."
  }
}

output "groups" {
  description = "Every Entra group, by display name: Administrators, and one per team."
  value = merge(
    { Administrators = "Administers everything." },
    { for team in local.teams : team.title => team.comment },
  )

  # A team of that name would be merged into it, with every grant Administrators has
  precondition {
    condition     = !contains([for team in local.teams : team.title], "Administrators")
    error_message = "No team may be titled Administrators."
  }
}
