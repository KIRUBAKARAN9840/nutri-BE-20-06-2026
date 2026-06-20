# ═════════════════════════════════════════════════════════════════════
# DATA TIER — ElastiCache Redis in private subnets
# ═════════════════════════════════════════════════════════════════════

resource "aws_elasticache_subnet_group" "main" {
  name       = "${local.name_prefix}-redis-subnet-group"
  subnet_ids = aws_subnet.private[*].id

  tags = { Name = "${local.name_prefix}-redis-subnet-group" }
}

resource "aws_elasticache_cluster" "main" {
  cluster_id           = "${local.name_prefix}-redis"
  engine               = "redis"
  engine_version       = "7.1"
  node_type            = "cache.t4g.small"  # ~$22/mo — right-sized for staging
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  port                 = 6379

  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.redis.id]

  snapshot_retention_limit = 7        # 7-day snapshot retention
  snapshot_window          = "20:00-21:00" # UTC — before RDS backup window

  apply_immediately = true # staging — for prod, set false to wait for maintenance window

  tags = { Name = "${local.name_prefix}-redis" }
}
