output "name_servers" {
  value = {
    for zone in [aws_route53_zone.titmuss_au] : zone.name => zone.name_servers
  }
}
