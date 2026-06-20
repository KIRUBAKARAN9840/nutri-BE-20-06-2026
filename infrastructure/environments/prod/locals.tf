locals {
  # The single source of truth for all resource IDs we're importing.
  # Sourced from AWS_INVENTORY_LIVE.md (verified 2026-05-13).
  imported = {
    vpc_id           = "vpc-0f268fb3dc0dd0600"
    vpc_cidr         = "10.0.0.0/16"
    igw_id           = "igw-0709a4e3db58ecc48"
    nat_gateway_id   = "nat-04867289b85560c1e"
    s3_vpc_endpoint  = "vpce-0d53cf8d2a4c3829e"

    subnet_public_2a   = "subnet-06fd7a321c77c1def"   # NAT lives here
    subnet_public_2b   = "subnet-00c1b09f6f02aff10"
    subnet_public_2c   = "subnet-04e1a6607004b72a9"
    subnet_private_2a  = "subnet-0c2101a6a7e7bf803"   # RDS primary
    subnet_private_2b  = "subnet-0b59975c322d501c6"
    subnet_private_2c  = "subnet-0d9f1514dcdf67874"   # Redis

    rds_identifier     = "devfittbotdb"
    redis_cluster_id   = "fittbot-dev-cluster-new"
    ecs_cluster_name   = "dev-codedeploy-cluster-test"
    alb_name           = "dev-lb-new"

    ecr_repo_backend   = "fittbot/backend"
    ecr_repo_pg        = "fittbot/paymentgateway"
  }

  common_tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}
