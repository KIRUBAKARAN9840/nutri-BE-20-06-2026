terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.30"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.13"
    }
    kubectl = {
      source  = "alekc/kubectl"
      version = "~> 2.0"
    }
  }
  # ---------------------------------------------------------------------------
  # Remote state — enable BEFORE a second person ever runs terraform apply.
  # The S3 bucket must have versioning + SSE-KMS + Block Public Access ON.
  # The DynamoDB table prevents concurrent applies from corrupting state.
  # ---------------------------------------------------------------------------
  # backend "s3" {
  #   bucket         = "fymble-tfstate-<account-id>"
  #   key            = "eks/fymble-prod/terraform.tfstate"
  #   region         = "ap-south-2"
  #   dynamodb_table = "fymble-tfstate-locks"
  #   encrypt        = true
  #   kms_key_id     = "alias/fymble-tfstate"
  # }
}
