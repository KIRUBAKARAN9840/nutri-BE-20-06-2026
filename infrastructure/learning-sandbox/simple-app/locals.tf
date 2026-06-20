locals {
  name_prefix = "${var.project}-${var.environment}" # → "fittbot-staging"

  common_tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
    Owner       = "naveenkulandasamy"
  }
}
