# ─────────────────────────────────────────────────────────────────────────
# Import blocks — ElastiCache Redis `fittbot-dev-cluster-new`
#
# Phase 3.8. cache.r7g.large, Redis 7.1, single-node in 2c.
# ─────────────────────────────────────────────────────────────────────────

# import {
#   to = module.redis.aws_elasticache_cluster.main
#   id = "fittbot-dev-cluster-new"
# }
#
# import {
#   to = module.redis.aws_elasticache_subnet_group.main
#   id = "redis-subnet-group"
# }
#
# # Parameter group is the AWS default `default.redis7` — same situation as RDS.
# # Plan to create a custom one if you need to change maxmemory-policy etc.
