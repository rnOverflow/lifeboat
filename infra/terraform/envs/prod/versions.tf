terraform {
  required_version = ">= 1.6"
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 5.70" }
    azurerm = { source = "hashicorp/azurerm", version = "~> 4.5" }
    random  = { source = "hashicorp/random", version = "~> 3.6" }
  }
  # Remote state: configure with `terraform init -backend-config=backend.hcl` (see backend.hcl.example)
  backend "s3" {}
}

provider "aws" {
  region = var.aws_region
  default_tags { tags = local.tags }
}

provider "azurerm" {
  features {}
  subscription_id = var.azure_subscription_id
}
