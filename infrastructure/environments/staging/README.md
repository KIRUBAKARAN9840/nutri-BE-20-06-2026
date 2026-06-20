# Staging environment — placeholder

The `staging-cluster` ECS cluster exists in AWS but has zero running tasks. Not backfilling it for now.

If/when staging becomes active:

1. Copy `environments/prod/` to `environments/staging/`
2. Adjust `backend.tf` to use a different state key (`staging/terraform.tfstate`)
3. Adjust `terraform.tfvars` for staging-specific values
4. Use the same `modules/*` — that's the point of modules
