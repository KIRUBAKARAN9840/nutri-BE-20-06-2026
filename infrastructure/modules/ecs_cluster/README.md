# ECS Cluster module — STUB

Will mirror `dev-codedeploy-cluster-test`.

## Resources

- `aws_ecs_cluster.main` — the cluster
- `aws_ecs_cluster_capacity_providers.main` — Fargate, Fargate Spot

## Settings to mirror

```hcl
setting {
  name  = "containerInsights"
  value = "enabled"   # was 'enhanced' on staging-cluster (which has 0 tasks — costly)
}
```

## Rename suggestion

The cluster name `dev-codedeploy-cluster-test` runs production traffic. Don't rename via Terraform import — instead, plan a separate migration to a `fittbot-prod-ecs` cluster once everything is in IaC. Renaming forces destroy+recreate.
