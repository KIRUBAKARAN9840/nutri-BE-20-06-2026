locals {
  cluster_name = var.cluster_name

  common_tags = {
    Project     = "Fymble"
    Environment = var.environment
    ManagedBy   = "Terraform"
    Owner       = var.owner
  }
}
