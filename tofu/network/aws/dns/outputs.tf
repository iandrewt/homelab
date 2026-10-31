output "name_servers" {
  value = {
    for zone in [aws_route53_zone.titmuss_au] : zone.name => zone.name_servers
  }
}

# For the `homelab-octodns` profile octoDNS writes Route53 as
output "octodns_role_arn" {
  value = aws_iam_role.octodns.arn
}
