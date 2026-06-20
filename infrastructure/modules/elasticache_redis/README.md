# ElastiCache Redis module — STUB

Will mirror `fittbot-dev-cluster-new`. Generate HCL via `terraform plan -generate-config-out=`.

**Critical lifecycle:** apply `prevent_destroy = true` on `aws_elasticache_cluster.main`.

## Resources

- `aws_elasticache_cluster.main` — `fittbot-dev-cluster-new`, `cache.r7g.large`, Redis 7.1
- `aws_elasticache_subnet_group.main` — `redis-subnet-group`
- `aws_elasticache_parameter_group.main` — currently uses `default.redis7` (can't import; create custom if you need to change `maxmemory-policy`, `appendonly`, etc.)

## After backfill, separate PR for right-sizing

The audit (see `REDIS_SIZING_REPORT.md`) showed this instance uses 12 MB of 13 GB (0.09 %). To safely resize:

1. Snapshot first (`aws elasticache create-snapshot`)
2. Enable auto-snapshots (currently disabled!)
3. Change `node_type = "cache.t4g.small"` in Terraform
4. Run `terraform plan` — confirm only "1 to change" on node_type
5. Apply — RDS-like blue/green migration internally, 5–15 min downtime
