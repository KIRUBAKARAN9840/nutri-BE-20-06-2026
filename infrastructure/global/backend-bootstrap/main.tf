# ─────────────────────────────────────────────────────────────────────────
# Terraform State Backend Bootstrap
#
# This config is run ONCE with local state to create:
#   - S3 bucket for remote Terraform state (versioned, encrypted, private)
#   - DynamoDB table for state locking (prevents concurrent applies)
#
# After this is applied successfully:
#   - The S3 bucket name (fittbot-tfstate-ap-south-2) is referenced by
#     environments/*/backend.tf
#   - The DynamoDB table (terraform-state-lock) holds locks per state file
#
# DO NOT delete this stack — the resources here are the foundation for every
# other Terraform run. If you ever need to destroy them, you must first
# migrate all environment state files OUT of S3.
# ─────────────────────────────────────────────────────────────────────────

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # State for this bootstrap is INTENTIONALLY local. Don't change this.
  # backend "local" {}  (implicit)
}

provider "aws" {
  region = "ap-south-2"

  default_tags {
    tags = {
      Project     = "fittbot"
      Environment = "global"
      Component   = "terraform-state-backend"
      ManagedBy   = "terraform"
      Owner       = "naveenkulandasamy"
    }
  }
}

# ─────────────────────────────────────────────────────────────────────────
# S3 bucket for Terraform state files
# ─────────────────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "tfstate" {
  bucket = "fittbot-tfstate-ap-south-2"

  lifecycle {
    # Refuse to destroy — even if someone runs `terraform destroy`,
    # this bucket holds the state of every environment.
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  rule {
    id     = "expire-old-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

# ─────────────────────────────────────────────────────────────────────────
# DynamoDB table for state locking
# ─────────────────────────────────────────────────────────────────────────

resource "aws_dynamodb_table" "tfstate_lock" {
  name         = "terraform-state-lock"
  billing_mode = "PAY_PER_REQUEST" # cheap; 0 LCUs when idle
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }
}

# ─────────────────────────────────────────────────────────────────────────
# Outputs — referenced from environment backend.tf
# ─────────────────────────────────────────────────────────────────────────

output "tfstate_bucket" {
  value       = aws_s3_bucket.tfstate.bucket
  description = "S3 bucket name for terraform state files"
}

output "tfstate_lock_table" {
  value       = aws_dynamodb_table.tfstate_lock.name
  description = "DynamoDB table for state locking"
}

output "region" {
  value       = "ap-south-2"
  description = "AWS region where state lives"
}
