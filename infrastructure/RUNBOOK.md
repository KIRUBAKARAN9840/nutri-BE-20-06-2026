# Terraform Backfill Runbook — Safe Import Procedure

**Goal:** Replicate every existing AWS resource (`ap-south-2`, account `182399696098`) into Terraform code, with **zero changes to production**.

**Hard rule:** Do not type `terraform apply` for any resource until `terraform plan` shows `Plan: 0 to add, 0 to change, 0 to destroy`. If you see ANY destroy/replace, fix the HCL — never apply.

---

## Phase 0 — Prerequisites (15 min, one-time)

### a) Install Terraform

```bash
brew install terraform
terraform version    # must be ≥ 1.5 for `import` blocks
```

### b) Widen IAM permissions for the import phase

```bash
# Adds ReadOnlyAccess to fittbot-aws-cli — fully reversible
aws iam attach-user-policy \
  --user-name fittbot-aws-cli \
  --policy-arn arn:aws:iam::aws:policy/ReadOnlyAccess
```

After import is complete, detach with `aws iam detach-user-policy ...` and switch to a least-privilege Terraform role.

### c) Verify access

```bash
aws sts get-caller-identity         # confirms profile is right
aws ec2 describe-vpcs --region ap-south-2 --output table | head -10
```

---

## Phase 1 — Bootstrap state backend (30 min, one-time)

The state backend (S3 + DynamoDB) is the chicken-and-egg problem: we need somewhere to store Terraform state, but creating it in Terraform requires state. Solution: bootstrap it with **local state**, then migrate.

```bash
cd infrastructure/global/backend-bootstrap

terraform init                      # uses local state by default
terraform plan                       # review — should show:
                                     #   - 1 S3 bucket
                                     #   - 1 DynamoDB table
                                     #   - bucket versioning + encryption + public-block

# When the plan looks right (only "to add", no "to change/destroy"):
terraform apply                      # creates the bucket and lock table

# Verify
aws s3api head-bucket --bucket fittbot-tfstate-ap-south-2 --region ap-south-2
aws dynamodb describe-table --region ap-south-2 --table-name terraform-state-lock | head -5
```

The local `terraform.tfstate` file in this folder is now obsolete — but **keep it** in case you ever need to destroy the backend. Add `terraform.tfstate*` to `.gitignore` so you don't commit it. (Already done.)

---

## Phase 2 — Initialize prod environment (15 min)

```bash
cd ../../environments/prod

# This downloads providers and points at the S3 backend
terraform init

# Sanity check — confirms the backend works and state is empty
terraform plan -refresh-only
# Expected output: "No changes." (state is empty, no resources tracked yet)
```

---

## Phase 3 — Import in safe order (multiple sessions, ~3–5 days total)

**Order matters** because some resources reference others (subnets reference VPC, ECS services reference cluster + task def + ALB target group). Import dependencies first.

| # | Module | Resources | Risk | Time |
|---|---|---|---|---|
| 3.1 | `iam/` | 4 KMS keys | 🟢 Low | 30 min |
| 3.2 | `networking/` | VPC, 6 subnets, IGW, NAT, 3 RTs, 1 VPC endpoint | 🟢 Low | 1 hr |
| 3.3 | `secrets/` | 7 Secrets Manager entries (metadata only, NOT values) | 🟢 Low | 30 min |
| 3.4 | `ecr/` | 2 ECR repos + lifecycle policies | 🟢 Low | 15 min |
| 3.5 | `alb/` | 1 ALB, 14 TGs, 1 listener, listener rules | 🟡 Medium | 2 hr |
| 3.6 | `waf/` | WAFv2 Web ACL + association | 🟡 Medium | 1 hr |
| 3.7 | `rds_mysql/` | `devfittbotdb` + parameter group + subnet group | 🟠 High | 2 hr |
| 3.8 | `elasticache_redis/` | `fittbot-dev-cluster-new` + subnet group | 🟠 High | 1 hr |
| 3.9 | `ecs_cluster/` | Cluster + container insights setting | 🟡 Medium | 30 min |
| 3.10 | `ecs_service/` | 7 services × (task def + service) | 🟡 Medium | 3 hr |
| 3.11 | `observability/` | 110 log groups + alarms | 🟢 Low | 1 hr |
| 3.12 | Lambdas + EventBridge | 8 lambdas + 4 rules | 🟢 Low | 1 hr |

### The pattern (repeat for every resource group)

```bash
# 1. Edit imports/<module>.tf to add import blocks like:
#    import {
#      to = module.networking.aws_vpc.main
#      id = "vpc-0f268fb3dc0dd0600"
#    }

# 2. Generate the matching HCL automatically (Terraform reads from AWS):
terraform plan -generate-config-out=generated/<module>.tf

# 3. Move the generated HCL into modules/<module>/main.tf (clean it up: variables, locals, descriptions)

# 4. Plan again WITHOUT -generate-config-out:
terraform plan

# 5. Iterate on the HCL until plan says:
#    Plan: 0 to add, 0 to change, 0 to destroy

# 6. THEN apply (which only writes to state, no AWS changes):
terraform apply

# 7. Verify in AWS console — nothing should look different
```

### Why this is safe at every step

| Command | What it does to AWS |
|---|---|
| `terraform init` | Downloads providers. No AWS calls. |
| `terraform plan` | **Read-only** AWS describe calls. No changes. |
| `terraform plan -generate-config-out` | **Read-only** AWS describes + writes HCL to local file. No changes. |
| `terraform plan -refresh-only` | Read-only refresh of state from AWS. No changes. |
| `terraform import` (CLI) | **Read-only** AWS, writes to local state. No changes. |
| `terraform apply` (only after clean plan) | **Only triggers AWS changes that plan promised**. Clean plan = zero changes = zero risk. |

---

## Phase 4 — The "danger zones" and how to defuse them

These resources have non-trivial config that Terraform might want to "fix" (drift). Be extra careful:

### RDS `devfittbotdb`
- **Drift risk:** `apply_immediately`, parameter group changes, backup window, maintenance window
- **Mitigation:** Add `lifecycle { ignore_changes = [parameter_group_name, ...] }` if the imported config differs from your HCL
- **Safety net:** `lifecycle { prevent_destroy = true }` — Terraform will refuse to destroy this even if asked

### ElastiCache `fittbot-dev-cluster-new`
- **Drift risk:** Parameter group, snapshot window. **No snapshots currently exist** — taking one as part of this is recommended (separate from Terraform).
- **Mitigation:** Match the parameter group name exactly; use `default.redis7`

### ALB target groups
- **Drift risk:** Health check intervals/thresholds — defaults differ between console and Terraform
- **Mitigation:** Explicitly write every health check field; don't rely on defaults

### ECS services with CodeDeploy Blue/Green
- **Drift risk:** Active deployment in progress, capacity provider strategies, deployment circuit breaker config
- **Mitigation:** `lifecycle { ignore_changes = [task_definition, desired_count] }` so CodeDeploy can keep doing its job

---

## Phase 5 — Remove the imports/ folder (cleanup)

Once every resource is imported and plan is clean:

```bash
cd environments/prod
rm -rf imports/
git add -A
git commit -m "Backfill: all infra now in Terraform, imports complete"
```

The `import` blocks have done their job. Only `modules/` calls + state remain.

---

## Phase 6 — Continuous safety (the MNC pattern)

```bash
# Add to .github/workflows/terraform-plan.yml
# Every PR auto-runs `terraform plan` and posts the diff as a comment.
# No PR is merged without a clean plan.
```

This is **drift detection** — what separates a mature IaC setup from "we set this up once":

1. **Nightly `terraform plan`** in CI — alerts if console state ≠ HCL
2. **Branch protection** — main can only be merged into via PR with passing plan
3. **CODEOWNERS** — infra changes require Naveen's approval
4. **`prevent_destroy` lifecycle** on all stateful resources (RDS, Redis, S3 buckets, KMS keys)

---

## What to do if something goes wrong

| Symptom | Action |
|---|---|
| `terraform plan` shows "1 to destroy" on a resource | **STOP.** Don't apply. Fix the HCL to match reality. Use `-generate-config-out` if needed. |
| `terraform import` fails with "AccessDenied" | Widen IAM perms (see Phase 0b). Re-run import. |
| `terraform plan` shows "1 to change" on a sensitive resource (RDS class, Redis node type) | The HCL says something different from AWS. **Fix the HCL**, never the AWS resource. |
| State file gets corrupted | The S3 backend has versioning on. Roll back: `aws s3api list-object-versions --bucket fittbot-tfstate-ap-south-2 --prefix prod/terraform.tfstate` → restore the previous version |
| You imported the wrong resource | `terraform state rm <address>` removes from state (does NOT touch AWS) |
| Someone makes a change via AWS console after import | Next `terraform plan` will detect it. Either pull the change into HCL (`terraform plan -refresh-only`) or undo in console. |

---

## When you're done

```
infrastructure/environments/prod/
├── backend.tf
├── providers.tf
├── versions.tf
├── variables.tf
├── locals.tf
├── main.tf                # calls 11 modules — no resources defined here
├── outputs.tf
└── terraform.tfvars
```

That's a production-grade IaC repo. **Total LOC: ~500–800 lines for an app this size.** Compare to the 4,000+ lines that a Terragrunt+modules-everywhere approach generates — DRY without being over-engineered.

---

## Estimated total effort

- Phase 0 (setup): 30 min
- Phase 1 (bootstrap): 30 min
- Phase 2 (init): 15 min
- Phase 3 (import resources): **3–5 working days**, broken across sessions
- Phase 4 (lifecycle hardening): 1 day
- Phase 5 (cleanup): 30 min
- Phase 6 (CI setup): 1 day

**Total: ~1 week solo, with each step independently reversible.**

You can stop after Phase 3 and have ~80% of the benefit. Phase 4+ are operational polish.
