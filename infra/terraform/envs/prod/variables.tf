variable "aws_region" {
  type    = string
  default = "us-east-1"
}
variable "azure_subscription_id" {
  type = string
}
variable "azure_location" {
  type    = string
  default = "eastus"
}
variable "zone_name" {
  type = string
}
variable "record_name" {
  type = string
}
variable "aws_endpoint_host" {
  type = string
}
variable "azure_endpoint_host" {
  type = string
}
variable "azure_egress_cidrs" {
  description = "Azure NAT egress CIDRs allowed to subscribe to the AWS primary"
  type        = list(string)
  default     = []
}
variable "eks_oidc_provider_arn" {
  type    = string
  default = ""
}
variable "aks_oidc_issuer_url" {
  type    = string
  default = ""
}
