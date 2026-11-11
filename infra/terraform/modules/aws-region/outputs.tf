output "db_instance_id" { value = aws_db_instance.primary.identifier }
output "db_endpoint" { value = aws_db_instance.primary.address }
output "db_master_secret_arn" {
  value     = aws_db_instance.primary.master_user_secret[0].secret_arn
  sensitive = true
}
output "backup_bucket" { value = aws_s3_bucket.backups.bucket }
output "control_plane_role_arn" { value = try(aws_iam_role.control_plane[0].arn, null) }
