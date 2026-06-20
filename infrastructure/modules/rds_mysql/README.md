# RDS MySQL module — STUB

Will mirror `devfittbotdb`. To complete:

1. Uncomment `module.rds_mysql` in `environments/prod/main.tf`
2. Uncomment imports in `environments/prod/imports/rds.tf`
3. Run:
   ```
   terraform plan -generate-config-out=generated/rds_mysql.tf
   ```
4. Move the generated HCL into `modules/rds_mysql/main.tf`, clean up to use variables.
5. Iterate `terraform plan` until: `0 to add, 0 to change, 0 to destroy`.
6. Add `lifecycle { prevent_destroy = true }` on `aws_db_instance.main`.

**Critical lifecycle config for an RDS that's already in use:**

```hcl
lifecycle {
  prevent_destroy       = true
  ignore_changes        = [
    password,             # Managed in Secrets Manager
    snapshot_identifier,  # Don't track snapshots in tf
  ]
}
```

## Resources to manage

- `aws_db_instance.main` — the instance itself
- `aws_db_subnet_group.main` — `devfittbotdb-subnet-group`
- `aws_db_parameter_group.main` — **create a custom one**; can't import `default.mysql8.0`
- `aws_db_option_group.main` — `default:mysql-8-0` (also AWS-default, skip)
- `aws_security_group.rds` — `sg-002d102fa9037d7ed`

## Future improvements (don't include in initial backfill)

- gp2 → gp3 storage migration (saves ~$0.50/mo + 50× IOPS)
- Move backup window from 11:24 UTC to 21:30 UTC (3:00 AM IST)
- Move maintenance window from Thu 13:53 UTC to Sun 22:30 UTC
- Enable Performance Insights (free)
