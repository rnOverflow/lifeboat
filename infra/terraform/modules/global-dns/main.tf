data "aws_route53_zone" "this" {
  name         = var.zone_name
  private_zone = false
}

# Independent health checks for both clouds; their status feeds Lifeboat and CloudWatch alarms.
resource "aws_route53_health_check" "aws" {
  fqdn              = var.aws_endpoint_host
  type              = "HTTPS"
  resource_path     = var.health_path
  port              = 443
  request_interval  = 10
  failure_threshold = 3
  tags              = { Name = "lifeboat-aws" }
}

resource "aws_route53_health_check" "azure" {
  fqdn              = var.azure_endpoint_host
  type              = "HTTPS"
  resource_path     = var.health_path
  port              = 443
  request_interval  = 10
  failure_threshold = 3
  tags              = { Name = "lifeboat-azure" }
}

# Single CNAME owned by Lifeboat: failover is an explicit, approved, audited UPSERT (controlled failover),
# not an automatic DNS flip. Terraform only seeds it; Lifeboat owns the value afterwards.
resource "aws_route53_record" "app" {
  zone_id = data.aws_route53_zone.this.zone_id
  name    = var.record_name
  type    = "CNAME"
  ttl     = var.ttl
  records = [var.aws_endpoint_host]
  lifecycle { ignore_changes = [records] }
}
