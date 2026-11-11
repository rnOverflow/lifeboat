output "resource_group" { value = azurerm_resource_group.this.name }
output "pg_server_name" { value = azurerm_postgresql_flexible_server.standby.name }
output "pg_fqdn" { value = azurerm_postgresql_flexible_server.standby.fqdn }
output "key_vault_uri" { value = azurerm_key_vault.this.vault_uri }
output "backup_storage_url" { value = azurerm_storage_account.backups.primary_blob_endpoint }
output "control_plane_client_id" { value = azurerm_user_assigned_identity.control_plane.client_id }
