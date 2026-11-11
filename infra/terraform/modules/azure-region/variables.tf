variable "name" {
  type = string
}
variable "location" {
  type    = string
  default = "eastus"
}
variable "vnet_cidr" {
  type    = string
  default = "10.50.0.0/16"
}
variable "pg_sku" {
  type    = string
  default = "GP_Standard_D4ds_v5"
}
variable "pg_storage_mb" {
  type    = number
  default = 131072
}
variable "aks_oidc_issuer_url" {
  description = "OIDC issuer of the AKS cluster running the control plane (workload identity). Empty to skip."
  type        = string
  default     = ""
}
variable "tags" {
  type    = map(string)
  default = {}
}
