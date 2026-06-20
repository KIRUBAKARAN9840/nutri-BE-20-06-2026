# Fittbot / Fymble — Infrastructure as Code (Terraform)

Backfill of the existing AWS production environment in `ap-south-2` into Terraform, **with zero destructive operations**.

| | |
|---|---|
| **Approach** | Terraform 1.5+ `import` blocks (declarative, plan-before-apply) |
| **Style** | Modular monolith for infra — environments + reusable modules |
| **State backend** | S3 + DynamoDB lock table (created via one-time bootstrap) |
| **Workflow** | Plan-only until `0 to add / 0 to change / 0 to destroy` for every resource |

---

## Folder layout (MNC pattern: environment overlay + reusable modules)

```
infrastructure/
├── README.md                    # this file
├── RUNBOOK.md                   # step-by-step safe-import procedure
├── .gitignore
│
├── global/                      # account-wide resources (run once)
│   └── backend-bootstrap/       # creates the S3 bucket + DynamoDB lock table
│
├── environments/                # one folder per environment
│   ├── prod/                    # the live one we're backfilling
│   │   ├── backend.tf           # remote state config
│   │   ├── providers.tf         # AWS provider, version pins
│   │   ├── versions.tf
│   │   ├── variables.tf
│   │   ├── locals.tf
│   │   ├── main.tf              # composition — calls modules
│   │   ├── outputs.tf
│   │   ├── terraform.tfvars
│   │   └── imports/             # one file per resource category
│   │       ├── networking.tf    # VPC, subnets, NAT, IGW, RT
│   │       ├── ecs.tf           # cluster + services + task defs
│   │       ├── rds.tf           # devfittbotdb
│   │       ├── redis.tf         # fittbot-dev-cluster-new
│   │       ├── alb.tf           # dev-lb-new + target groups + listeners
│   │       ├── waf.tf           # Web ACL
│   │       ├── lambda.tf        # 8 Lambda functions
│   │       ├── eventbridge.tf
│   │       ├── secrets.tf
│   │       ├── kms.tf
│   │       └── ecr.tf
│   └── staging/                 # dormant cluster — leave for later
│
└── modules/                     # reusable building blocks (one per AWS service domain)
    ├── networking/              # VPC + 6 subnets + IGW + NAT + route tables
    ├── ecs_cluster/             # cluster + container insights
    ├── ecs_service/             # one service + task def + autoscaling
    ├── rds_mysql/               # RDS MySQL with snapshots + params
    ├── elasticache_redis/       # Redis with snapshots + params
    ├── alb/                     # ALB + target groups + listeners + rules
    ├── waf/                     # Regional WAFv2 Web ACL
    ├── observability/           # CloudWatch alarms + log groups + retention
    ├── iam/                     # task roles + execution roles
    ├── ecr/                     # ECR repositories + lifecycle policies
    └── secrets/                 # Secrets Manager + KMS keys
```

---

## Why this exact structure (and why MNCs use it)

| Layer | Purpose | Real-world examples |
|---|---|---|
| `global/` | Resources that exist once per AWS account (state backend, IAM org roles, Route 53 zones) | HashiCorp, Gruntwork's standard pattern |
| `environments/<env>/` | Per-environment composition. Each is **deployable independently** with its own state file. | Stripe, Atlassian, Datadog all use env folders |
| `modules/` | Versionable building blocks. **Same module, different env tfvars** → DRY infra | Terraform Registry style |
| `imports/` (under prod) | Keeps import statements **separate from resource HCL** so they're easy to remove after backfill | Standard practice in import-heavy migrations |

Once backfill is complete, you delete `imports/` and only `main.tf` + module calls remain. This is what production Terraform repos look like at scale.

---

## Bootstrap dependency chain

```
1. global/backend-bootstrap/     ──> creates S3 bucket + DynamoDB table for state
                                     (using LOCAL state, just this once)
                  │
                  ▼
2. environments/prod/             ──> uses the S3 backend → starts importing real resources
```

After bootstrap, all `terraform` runs in `environments/prod/` write state to S3 with DynamoDB locking — the standard MNC pattern that lets multiple engineers run plans safely.

---

## Safety guarantees (why this CANNOT damage your prod)

1. **`terraform import` is read-only on AWS** — it only reads, never writes/modifies/deletes anything in AWS. It writes ONLY to local Terraform state.
2. **`terraform plan` is read-only on AWS** — describes intended changes but applies nothing.
3. **No `terraform apply` is run until plan shows `0 to add, 0 to change, 0 to destroy`** for every imported resource.
4. **`lifecycle { prevent_destroy = true }`** added on critical resources (RDS, Redis, S3 buckets) so even an accidental destroy is refused by Terraform.
5. **State backend is a separate bootstrap** — even if your prod tf state ever corrupts, the resources keep running. AWS doesn't care about Terraform state.

The **only** way this can hurt prod is if you (a) write bad HCL, (b) ignore the plan output saying "will destroy 1", and (c) type `yes` at the apply prompt. The workflow below prevents all three.

---

## Start here — read in order

1. [`RUNBOOK.md`](RUNBOOK.md) — the exact step-by-step safe-import procedure
2. [`global/backend-bootstrap/README.md`](global/backend-bootstrap/README.md) — set up the state backend once
3. [`environments/prod/README.md`](environments/prod/README.md) — the prod composition
4. [`modules/networking/README.md`](modules/networking/README.md) — the first module to import (lowest risk)

---

## Required IAM permissions

Your current `fittbot-aws-cli` user has limited perms (no WAF/SSM/SQS/Cost Explorer). For Terraform import you need much broader **read** permissions.

**Minimum:** attach the AWS-managed policy **`ReadOnlyAccess`** to `fittbot-aws-cli`.

**Better (for actual ops):** create a dedicated `terraform-prod` IAM user with **`PowerUserAccess`** + IAM-specific policies. This is what MNCs do — Terraform never runs as a human user.

```bash
# Quick read-only attach for import phase
aws iam attach-user-policy --user-name fittbot-aws-cli \
  --policy-arn arn:aws:iam::aws:policy/ReadOnlyAccess
```

(That command itself is reversible — `detach-user-policy` to undo.)

---

## What this Terraform code includes

✅ Every resource from [`AWS_INVENTORY_LIVE.md`](../AWS_INVENTORY_LIVE.md) listed with its real ID, ready to import.

✅ Pre-written `import` blocks (Terraform 1.5+ syntax) for:
- VPC `vpc-0f268fb3dc0dd0600` + 6 subnets + NAT + IGW + route tables
- ECS cluster `dev-codedeploy-cluster-test` + 7 services + their task definitions
- RDS `devfittbotdb`
- ElastiCache `fittbot-dev-cluster-new`
- ALB `dev-lb-new` + 14 target groups + listeners + rules
- 8 Lambda functions
- 4 EventBridge rules
- 7 Secrets Manager secrets + 4 KMS keys
- 2 ECR repositories
- 4 S3 buckets

✅ Module skeletons for each domain.

❌ Does **not** include code that creates new resources. The whole point is to mirror what exists.

---

## Quick-start (TL;DR)

```bash
# 0. Install Terraform 1.5+ if missing
brew install terraform

# 1. Bootstrap state backend (one time only)
cd global/backend-bootstrap
terraform init
terraform plan       # review — should show "1 bucket, 1 table to add"
terraform apply

# 2. Import production (iterative, safe)
cd ../../environments/prod
terraform init
terraform plan -refresh-only    # confirms backend works
terraform plan                   # will show resources to import

# 3. For each module, generate the HCL from real AWS state
terraform plan -generate-config-out=generated/networking.tf

# 4. Iterate until `terraform plan` says: 0 to add, 0 to change, 0 to destroy
# 5. THEN (and only then) commit the HCL + run apply (which is a no-op state update)
```

Full procedure in [`RUNBOOK.md`](RUNBOOK.md).
