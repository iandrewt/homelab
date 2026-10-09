locals {
  public_copies = toset(["alfheim.titmuss.zone"])
}

resource "aws_route53_zone" "public_copy" {
  for_each = local.public_copies

  name = each.key

  lifecycle {
    prevent_destroy = true
  }
}

data "cloudflare_zone" "titmuss_zone" {
  filter = { name = "titmuss.zone" }
}

resource "cloudflare_dns_record" "public_copy_ns" {
  for_each = {
    for pair in setproduct(local.public_copies, range(4)) :
    "${pair[0]}/${pair[1]}" => { zone = pair[0], index = pair[1] }
  }

  zone_id = data.cloudflare_zone.titmuss_zone.id
  name    = each.value.zone
  type    = "NS"
  content = aws_route53_zone.public_copy[each.value.zone].name_servers[each.value.index]
  ttl     = 3600
}

resource "cloudflare_dns_record" "public_copy_ds" {
  for_each = local.public_copies

  zone_id = data.cloudflare_zone.titmuss_zone.id
  name    = each.key
  type    = "DS"
  ttl     = 3600

  data = {
    key_tag     = aws_route53_key_signing_key.this[replace(each.key, ".", "_")].key_tag
    algorithm   = aws_route53_key_signing_key.this[replace(each.key, ".", "_")].signing_algorithm_type
    digest_type = aws_route53_key_signing_key.this[replace(each.key, ".", "_")].digest_algorithm_type
    digest      = aws_route53_key_signing_key.this[replace(each.key, ".", "_")].digest_value
  }

  depends_on = [aws_route53_hosted_zone_dnssec.this, cloudflare_dns_record.public_copy_ns]
}
