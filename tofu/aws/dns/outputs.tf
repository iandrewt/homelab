output "name_servers" {
  value = {
    for zone in [aws_route53_zone.titmuss_au, aws_route53_zone.acme] : zone.name => zone.name_servers
  }
}
