data "aws_availability_zones" "available" { state = "available" }
data "aws_caller_identity" "current" {}

locals {
  azs  = slice(data.aws_availability_zones.available.names, 0, 3)
  tags = merge(var.tags, { "lifeboat:role" = "primary" })
}

# ---------------------------------------------------------------- network
resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  tags                 = merge(local.tags, { Name = "${var.name}-vpc" })
}

resource "aws_subnet" "db" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)
  availability_zone = local.azs[count.index]
  tags              = merge(local.tags, { Name = "${var.name}-db-${local.azs[count.index]}" })
}

resource "aws_db_subnet_group" "this" {
  name       = "${var.name}-db"
  subnet_ids = aws_subnet.db[*].id
  tags       = local.tags
}

resource "aws_security_group" "db" {
  name        = "${var.name}-db"
  description = "PostgreSQL: app tier and cross-cloud logical replication"
  vpc_id      = aws_vpc.this.id
  tags        = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "db_vpc" {
  security_group_id = aws_security_group.db.id
  cidr_ipv4         = var.vpc_cidr
  from_port         = 5432
  to_port           = 5432
  ip_protocol       = "tcp"
  description       = "In-VPC clients"
}

resource "aws_vpc_security_group_ingress_rule" "db_replication" {
  for_each          = toset(var.allowed_replication_cidrs)
  security_group_id = aws_security_group.db.id
  cidr_ipv4         = each.value
  from_port         = 5432
  to_port           = 5432
  ip_protocol       = "tcp"
  description       = "Azure standby logical replication subscriber"
}

# ---------------------------------------------------------------- database
resource "aws_kms_key" "data" {
  description         = "${var.name} data encryption"
  enable_key_rotation = true
  tags                = local.tags
}

resource "aws_db_parameter_group" "pg" {
  name   = "${var.name}-pg16"
  family = "postgres16"
  parameter {
    name         = "rds.logical_replication"
    value        = "1"
    apply_method = "pending-reboot"
  }
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
  tags = local.tags
}

resource "aws_db_instance" "primary" {
  identifier                      = "${var.name}-primary"
  engine                          = "postgres"
  engine_version                  = "16.4"
  instance_class                  = var.db_instance_class
  allocated_storage               = var.db_allocated_storage
  max_allocated_storage           = var.db_allocated_storage * 4
  storage_type                    = "gp3"
  storage_encrypted               = true
  kms_key_id                      = aws_kms_key.data.arn
  db_name                         = "app"
  username                        = "lifeboat_admin"
  manage_master_user_password     = true # password lives in Secrets Manager, never in state or code
  multi_az                        = true
  db_subnet_group_name            = aws_db_subnet_group.this.name
  vpc_security_group_ids          = [aws_security_group.db.id]
  parameter_group_name            = aws_db_parameter_group.pg.name
  backup_retention_period         = 14
  backup_window                   = "03:00-04:00"
  copy_tags_to_snapshot           = true
  deletion_protection             = true
  skip_final_snapshot             = false
  final_snapshot_identifier       = "${var.name}-primary-final"
  performance_insights_enabled    = true
  performance_insights_kms_key_id = aws_kms_key.data.arn
  enabled_cloudwatch_logs_exports = ["postgresql"]
  auto_minor_version_upgrade      = true
  tags                            = local.tags
}

# ---------------------------------------------------------------- backup bucket (logical exports, reports)
resource "aws_s3_bucket" "backups" {
  bucket_prefix = "${var.name}-backups-"
  tags          = local.tags
}

resource "aws_s3_bucket_versioning" "backups" {
  bucket = aws_s3_bucket.backups.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "backups" {
  bucket                  = aws_s3_bucket.backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = 30 }
  }
}

# ---------------------------------------------------------------- control-plane IAM (least privilege, IRSA)
data "aws_iam_policy_document" "control_plane" {
  statement {
    sid       = "DescribeDatabases"
    actions   = ["rds:DescribeDBInstances", "rds:DescribeDBSnapshots"]
    resources = ["*"]
  }
  statement {
    sid       = "PromoteAndSnapshot"
    actions   = ["rds:PromoteReadReplica", "rds:CreateDBSnapshot", "rds:AddTagsToResource"]
    resources = [aws_db_instance.primary.arn, "arn:aws:rds:*:${data.aws_caller_identity.current.account_id}:snapshot:lifeboat-*"]
  }
  statement {
    sid       = "SteerTraffic"
    actions   = ["route53:ChangeResourceRecordSets", "route53:ListResourceRecordSets", "route53:GetChange"]
    resources = ["arn:aws:route53:::hostedzone/${var.route53_zone_id}", "arn:aws:route53:::change/*"]
  }
  statement {
    sid       = "BackupObjects"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.backups.arn, "${aws_s3_bucket.backups.arn}/*"]
  }
}

resource "aws_iam_policy" "control_plane" {
  name   = "${var.name}-lifeboat-control-plane"
  policy = data.aws_iam_policy_document.control_plane.json
  tags   = local.tags
}

data "aws_iam_policy_document" "irsa_trust" {
  count = var.eks_oidc_provider_arn == "" ? 0 : 1
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [var.eks_oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${replace(var.eks_oidc_provider_arn, "/^.*oidc-provider\\//", "")}:sub"
      values   = ["system:serviceaccount:lifeboat:lifeboat-api"]
    }
  }
}

resource "aws_iam_role" "control_plane" {
  count              = var.eks_oidc_provider_arn == "" ? 0 : 1
  name               = "${var.name}-lifeboat-control-plane"
  assume_role_policy = data.aws_iam_policy_document.irsa_trust[0].json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "control_plane" {
  count      = var.eks_oidc_provider_arn == "" ? 0 : 1
  role       = aws_iam_role.control_plane[0].name
  policy_arn = aws_iam_policy.control_plane.arn
}
