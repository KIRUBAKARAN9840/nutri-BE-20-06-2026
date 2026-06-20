# ECS Service module — STUB

This module represents ONE Fargate service. You'll instantiate it 7 times for the 7 production services.

## Inputs

- `cluster_id`
- `service_name`
- `task_def_family` (e.g., `Fittbot-Production`)
- `image` (full ECR URI)
- `cpu` and `memory` (in MiB)
- `desired_count`
- `target_group_arn` (for ALB-attached services)
- `subnet_ids`
- `security_group_ids`

## CodeDeploy Blue/Green warning

These services use CodeDeploy for Blue/Green deployments. **Set this lifecycle** to prevent Terraform from fighting CodeDeploy:

```hcl
lifecycle {
  ignore_changes = [
    task_definition,    # CodeDeploy rolls the task def
    desired_count,      # CodeDeploy adjusts during deploys
    load_balancer,      # CodeDeploy swaps blue/green TGs
  ]
}
```

Without this, every CodeDeploy deploy will cause `terraform plan` to show drift.
