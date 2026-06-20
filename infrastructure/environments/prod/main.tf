# ─────────────────────────────────────────────────────────────────────────
# Prod environment composition
#
# This file ONLY composes modules — no direct resources here.
# All actual resource definitions live in modules/*.
#
# Modules are added incrementally as we backfill from production AWS.
# Uncomment each `module` block as its resources are imported and verified.
# ─────────────────────────────────────────────────────────────────────────

# ── Phase 3.1: KMS (4 customer-managed keys)  ────────────────────────────
# module "kms" {
#   source = "../../modules/iam"
#   tags   = local.common_tags
# }

# ── Phase 3.2: Networking (VPC, subnets, NAT, IGW, route tables) ─────────
# module "networking" {
#   source = "../../modules/networking"
#
#   vpc_cidr            = local.imported.vpc_cidr
#   azs                 = ["ap-south-2a", "ap-south-2b", "ap-south-2c"]
#   public_subnet_cidrs = ["10.0.0.0/24", "10.0.1.0/24", "10.0.2.0/24"]
#   private_subnet_cidrs = ["10.0.10.0/24", "10.0.11.0/24", "10.0.12.0/24"]
#   tags = local.common_tags
# }

# ── Phase 3.3: Secrets Manager ───────────────────────────────────────────
# module "secrets" {
#   source = "../../modules/secrets"
#   tags   = local.common_tags
# }

# ── Phase 3.4: ECR ───────────────────────────────────────────────────────
# module "ecr" {
#   source = "../../modules/ecr"
#   repositories = [
#     local.imported.ecr_repo_backend,
#     local.imported.ecr_repo_pg,
#   ]
#   tags = local.common_tags
# }

# ── Phase 3.5: ALB + target groups + listeners ───────────────────────────
# module "alb" {
#   source = "../../modules/alb"
#
#   alb_name    = local.imported.alb_name
#   vpc_id      = module.networking.vpc_id
#   subnet_ids  = module.networking.public_subnet_ids
#   tags        = local.common_tags
# }

# ── Phase 3.7: RDS MySQL ─────────────────────────────────────────────────
# module "rds_mysql" {
#   source = "../../modules/rds_mysql"
#
#   identifier  = local.imported.rds_identifier
#   vpc_id      = module.networking.vpc_id
#   subnet_ids  = module.networking.private_subnet_ids
#   tags        = local.common_tags
# }

# ── Phase 3.8: ElastiCache Redis ─────────────────────────────────────────
# module "redis" {
#   source = "../../modules/elasticache_redis"
#
#   cluster_id  = local.imported.redis_cluster_id
#   vpc_id      = module.networking.vpc_id
#   subnet_ids  = module.networking.private_subnet_ids
#   tags        = local.common_tags
# }

# ── Phase 3.9 + 3.10: ECS Cluster + 7 services ───────────────────────────
# module "ecs_cluster" {
#   source       = "../../modules/ecs_cluster"
#   cluster_name = local.imported.ecs_cluster_name
#   tags         = local.common_tags
# }

# Service composition example (one block per service):
# module "ecs_service_api" {
#   source = "../../modules/ecs_service"
#
#   cluster_id     = module.ecs_cluster.cluster_id
#   service_name   = "Fittbot-Production"
#   task_def_arn   = "..."
#   desired_count  = 2
#   subnet_ids     = module.networking.private_subnet_ids
#   target_group_arn = module.alb.target_groups["fittbot-ecs-tg"]
#   tags           = local.common_tags
# }
