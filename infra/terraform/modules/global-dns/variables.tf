variable "zone_name" {
  description = "Existing public hosted zone, e.g. example.com"
  type        = string
}
variable "record_name" {
  description = "Customer-facing hostname steered by Lifeboat, e.g. app.example.com"
  type        = string
}
variable "aws_endpoint_host" {
  description = "Hostname of the AWS ALB / CloudFront distribution"
  type        = string
}
variable "azure_endpoint_host" {
  description = "Hostname of the Azure Front Door / Application Gateway endpoint"
  type        = string
}
variable "health_path" {
  type    = string
  default = "/healthz"
}
variable "ttl" {
  description = "Low TTL bounds DNS-cache contribution to RTO"
  type        = number
  default     = 30
}
