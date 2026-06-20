# Remote state backend — created by global/backend-bootstrap/
# Run `terraform init` after editing this file to migrate state.

terraform {
  backend "s3" {
    bucket         = "fittbot-tfstate-ap-south-2"
    key            = "prod/terraform.tfstate"
    region         = "ap-south-2"
    encrypt        = true
    dynamodb_table = "terraform-state-lock"
  }
}
