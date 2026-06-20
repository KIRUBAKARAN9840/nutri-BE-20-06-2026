provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "fittbot"
      Environment = "prod"
      ManagedBy   = "terraform"
      Owner       = "naveenkulandasamy"
    }
  }
}
