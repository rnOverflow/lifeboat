locals {
  name = "lifeboat-prod"
  tags = { project = "lifeboat", environment = "prod", managed_by = "terraform" }
}

module "dns" {
  source              = "../../modules/global-dns"
  zone_name           = var.zone_name
  record_name         = var.record_name
  aws_endpoint_host   = var.aws_endpoint_host
  azure_endpoint_host = var.azure_endpoint_host
}

module "aws_primary" {
  source                    = "../../modules/aws-region"
  name                      = local.name
  route53_zone_id           = module.dns.zone_id
  allowed_replication_cidrs = var.azure_egress_cidrs
  eks_oidc_provider_arn     = var.eks_oidc_provider_arn
  tags                      = local.tags
}

module "azure_secondary" {
  source              = "../../modules/azure-region"
  name                = local.name
  location            = var.azure_location
  aks_oidc_issuer_url = var.aks_oidc_issuer_url
  tags                = local.tags
}
