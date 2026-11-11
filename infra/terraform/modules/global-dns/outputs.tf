output "zone_id" { value = data.aws_route53_zone.this.zone_id }
output "record_fqdn" { value = aws_route53_record.app.fqdn }
output "health_check_ids" {
  value = { aws = aws_route53_health_check.aws.id, azure = aws_route53_health_check.azure.id }
}
