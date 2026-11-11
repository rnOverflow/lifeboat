# Feed these into the control plane's LIFEBOAT_* settings (provider_mode=cloud).
output "lifeboat_settings" {
  value = {
    LIFEBOAT_AWS_RDS_INSTANCE_ID   = module.aws_primary.db_instance_id
    LIFEBOAT_AWS_ROUTE53_ZONE_ID   = module.dns.zone_id
    LIFEBOAT_AWS_RECORD_NAME       = module.dns.record_fqdn
    LIFEBOAT_AWS_BACKUP_BUCKET     = module.aws_primary.backup_bucket
    LIFEBOAT_AZURE_RESOURCE_GROUP  = module.azure_secondary.resource_group
    LIFEBOAT_AZURE_PG_SERVER       = module.azure_secondary.pg_server_name
    LIFEBOAT_AZURE_STORAGE_ACCOUNT = module.azure_secondary.backup_storage_url
  }
}
