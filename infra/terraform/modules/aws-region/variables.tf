variable "name" {
  description = "Name prefix for all resources"
  type        = string
}
variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}
variable "db_instance_class" {
  type    = string
  default = "db.r6g.large"
}
variable "db_allocated_storage" {
  type    = number
  default = 100
}
variable "allowed_replication_cidrs" {
  description = "CIDRs (e.g. Azure standby egress IPs) allowed to connect for logical replication"
  type        = list(string)
  default     = []
}
variable "eks_oidc_provider_arn" {
  description = "OIDC provider ARN of the EKS cluster running the Lifeboat control plane (IRSA). Empty to skip."
  type        = string
  default     = ""
}
variable "route53_zone_id" {
  type = string
}
variable "tags" {
  type    = map(string)
  default = {}
}
