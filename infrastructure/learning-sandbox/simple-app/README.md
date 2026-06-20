# `simple-app` — Staging Infrastructure (the complete reference build)

A textbook 3-tier multi-AZ AWS architecture defined as IaC. **Don't run this casually** — EKS clusters alone cost ~₹6,000/month. This is a learning reference + a template for when you build real staging.

---

## What you're building

```
                              ┌── Internet ──┐
                                     │
                              ┌──────▼──────┐
                              │  Route 53   │  (you'd add this for real domain)
                              └──────┬──────┘
                                     │
                              ┌──────▼──────┐
                              │     ALB     │
                              │ public, 3AZ │
                              └──────┬──────┘
                                     │
              ╔══════════════════════▼══════════════════════╗
              ║  PRIVATE SUBNETS (10.20.10/11/12.0/24)       ║
              ║                                              ║
              ║  ┌──────────────────────────────────────┐    ║
              ║  │           EKS Cluster                │    ║
              ║  │  ┌──────┐  ┌──────┐  ┌──────┐        │    ║
              ║  │  │ Pod  │  │ Pod  │  │ Pod  │   ←──  │    ║   ◄── apps deployed via
              ║  │  │ API  │  │celery│  │chat- │        │    ║       kubectl/Helm (NOT Terraform)
              ║  │  └──────┘  └──────┘  │ diet │        │    ║
              ║  │                       └──────┘        │    ║
              ║  │  Managed Node Group: 2× t3.medium    │    ║
              ║  │  Spread across ap-south-2a/b/c       │    ║
              ║  └──────────────────────────────────────┘    ║
              ║                  │                           ║
              ║      ┌───────────┼───────────┐               ║
              ║      ▼           │           ▼               ║
              ║  ┌────────┐                ┌─────────┐       ║
              ║  │  RDS   │                │ Elasti- │       ║
              ║  │ MySQL  │                │  Cache  │       ║
              ║  │t4g.sml │                │ t4g.sml │       ║
              ║  └────────┘                └─────────┘       ║
              ╚══════════════════════════════════════════════╝
                                  │
              ╔═══════════════════▼══════════════════════════╗
              ║  PUBLIC SUBNETS (10.20.0/1/2.0/24)            ║
              ║                                              ║
              ║   NAT GW  (lets private subnets reach        ║
              ║   ▲       Razorpay / OpenAI / etc)           ║
              ║   │                                          ║
              ╚═══╪══════════════════════════════════════════╝
                  │
                  ▼
              ┌───────┐
              │  IGW  │ ◄── traffic to/from internet
              └───────┘
```

---

## File anatomy

| File | What it builds | Lines |
|---|---|---:|
| `versions.tf` | Pins Terraform + AWS provider versions | 11 |
| `providers.tf` | AWS region + default tags | 6 |
| `variables.tf` | Input variables (region, CIDRs, env, etc.) | 32 |
| `locals.tf` | Computed values (`name_prefix`, common tags) | 9 |
| `network.tf` | VPC + 6 subnets + IGW + NAT + 2 RTs + S3 VPCE | 100 |
| `security.tf` | 4 security groups (ALB, EKS nodes, RDS, Redis) | 90 |
| `alb.tf` | Load balancer + target group + listener | 42 |
| `eks.tf` | 2 IAM roles + cluster + managed node group | 100 |
| `rds.tf` | MySQL instance + subnet group | 33 |
| `redis.tf` | ElastiCache cluster + subnet group | 23 |
| `outputs.tf` | What this stack exposes | 30 |

**Total: ~480 lines of HCL** — that's the price tag for a full 3-tier multi-AZ AWS environment in code.

---

## How the pieces wire together (the dependency story)

The reference chain — what depends on what:

```
aws_vpc.main
  ├── aws_internet_gateway.main
  ├── aws_subnet.public[0..2]          ◄── 3 public subnets, one per AZ
  ├── aws_subnet.private[0..2]         ◄── 3 private subnets, one per AZ
  ├── aws_security_group.alb           ◄── ingress from internet (0.0.0.0/0:443)
  │       │
  │       └── aws_security_group.eks_nodes  ◄── ingress ONLY from ALB SG
  │              │
  │              ├── aws_security_group.rds   ◄── ingress ONLY from nodes SG
  │              └── aws_security_group.redis ◄── ingress ONLY from nodes SG
  │
  ├── aws_route_table.public           ◄── 0.0.0.0/0 → IGW
  └── aws_route_table.private          ◄── 0.0.0.0/0 → NAT
              │
              └── aws_nat_gateway.main ◄── in public[0]
                       │
                       └── aws_eip.nat ◄── elastic IP for NAT

aws_lb.main
  ├── subnets    = aws_subnet.public[*].id        ◄── ALB lives in public subnets
  └── sg         = aws_security_group.alb.id

aws_iam_role.eks_cluster + aws_eks_cluster.main
  └── subnets    = private + public               ◄── control plane ENIs need both

aws_iam_role.eks_nodes + aws_eks_node_group.main
  └── subnets    = aws_subnet.private[*].id       ◄── workers ONLY in private

aws_db_subnet_group.main + aws_db_instance.main
  └── subnets    = aws_subnet.private[*].id
  └── sg         = aws_security_group.rds.id

aws_elasticache_subnet_group.main + aws_elasticache_cluster.main
  └── subnets    = aws_subnet.private[*].id
  └── sg         = aws_security_group.redis.id
```

**Read this top-to-bottom — that's the order Terraform will create resources in.** Each layer depends on the one above. The dependency graph is built entirely from the `.id` references inside resource blocks.

---

## The "least privilege" pattern in security groups

This is the part that catches juniors. Look at the chain:

```hcl
# 1. ALB: open to internet on 80/443
ingress { cidr_blocks = ["0.0.0.0/0"] }

# 2. EKS nodes: open ONLY to ALB
ingress { security_groups = [aws_security_group.alb.id] }

# 3. RDS: open ONLY to EKS nodes
ingress { security_groups = [aws_security_group.eks_nodes.id] }

# 4. Redis: open ONLY to EKS nodes
ingress { security_groups = [aws_security_group.redis.id] }
```

**A pod cannot talk to RDS directly from outside the cluster. RDS cannot be reached from the internet. Period.** Each tier accepts traffic only from the tier directly above it.

This is what passes SOC2 / ISO 27001 audits. Same idea applies to the prod environment.

---

## What you'd actually run

⚠️ **Don't run this yet** — it provisions an EKS cluster (~$73/month for control plane + ~$50/month for 2 nodes + ~$30/month for RDS + ~$22/month for Redis = ~$175/month minimum). Total to provision once: ~₹15,000/month.

When you ARE ready:

```bash
cd "/Users/apple/Documents/Dev-admin/Fittbot Current AWS Version/infrastructure/learning-sandbox/simple-app"

# 1. Provide the DB password via env var (NEVER commit it)
export TF_VAR_rds_password='SomeStrongPasswordHere!2026'

# 2. Standard workflow
terraform init
terraform plan        # ~30 resources to add — read every one
terraform apply       # ~15-20 minutes (EKS cluster takes 10-12 min alone)

# After apply, configure kubectl to talk to the new cluster:
aws eks update-kubeconfig --region ap-south-2 --name fittbot-staging-eks
kubectl get nodes     # should show 2 nodes in 2 AZs

# Test ALB → cluster path:
curl http://$(terraform output -raw alb_dns_name)
# Returns 502 until you deploy an app — but the ALB itself is reachable
```

When done playing:

```bash
terraform destroy    # tears everything down (~15 min)
```

The `destroy` order is the dependency graph **reversed** — Redis/RDS first, then EKS nodes, then EKS cluster, then ALB, then subnets/NAT/IGW, finally VPC.

---

## What this teaches (the concepts)

| Concept | Where in the code |
|---|---|
| **Multi-AZ via `count`** | `aws_subnet.public` and `aws_subnet.private` — 3 of each, one per AZ |
| **Implicit dependencies via references** | `vpc_id = aws_vpc.main.id` everywhere — no `depends_on` needed |
| **Explicit dependencies for non-reference cases** | `depends_on = [aws_iam_role_policy_attachment.eks_cluster_policy]` on the cluster |
| **Layered security groups** | `security.tf` — chain of SGs, each only accepting from one above |
| **Splat expressions (`[*]`)** | `aws_subnet.public[*].id` — gets IDs of all public subnets as a list |
| **IAM trust + attachment pattern** | `eks.tf` — role + assume_role_policy + multiple policy_attachment resources |
| **Tags for cross-service discovery** | The `kubernetes.io/role/elb` tag on subnets — tells AWS Load Balancer Controller which subnets are valid for ingress |
| **Sensitive variables** | `var.rds_password` marked `sensitive = true` |
| **Output exposure** | `outputs.tf` — exposes endpoints for CI/kubectl |
| **Environment isolation via naming** | `local.name_prefix = "${var.project}-${var.environment}"` everywhere |

---

## What's NOT in this stack (intentionally — would add complexity)

| Feature | Why omitted | Where you'd add it |
|---|---|---|
| ACM certificate + HTTPS | Needs Route 53 zone + DNS | `aws_acm_certificate` + 443 listener |
| Route 53 records | Needs registered domain | `aws_route53_record` + `aws_route53_zone` |
| WAF | Adds $20+/month for staging | `aws_wafv2_web_acl` attached to ALB |
| Multi-AZ RDS | Staging cost saving | `multi_az = true` on `aws_db_instance.main` |
| Redis replication group | Staging cost saving | `aws_elasticache_replication_group` instead of `_cluster` |
| Cluster Autoscaler IRSA | EKS-specific deep dive | Helm chart + IAM role with `aws_iam_openid_connect_provider` |
| AWS Load Balancer Controller | Same | Same |
| CloudWatch alarms | Separate observability concern | `aws_cloudwatch_metric_alarm` |
| ECR repositories | Usually a separate stack | `aws_ecr_repository` |
| Secrets Manager + KMS | Separate state for sensitive | `aws_kms_key` + `aws_secretsmanager_secret` |

Each of these is a 50-100 line addition that you layer on as the environment matures.

---

## The mental model — how all of this works as one system

1. **Traffic flow** (a user request):
   - User → Route 53 → ALB → Listener → Target Group → Pod (in EKS) → RDS / Redis
   - Each hop is a separate AWS resource governed by separate IAM/SG rules

2. **Deploy flow** (you ship code):
   - Dev pushes → CI builds Docker image → push to ECR → `kubectl rollout` updates Deployment → EKS schedules new pods → ALB Target Group health-checks new pods → traffic shifts when healthy
   - **Terraform owns** the cluster + ALB + RDS + Redis. **Helm/kubectl owns** the pods.

3. **State flow** (something changes):
   - You edit `.tf` → `terraform plan` shows diff → review → `terraform apply` → resources change → state file updated
   - Or: kubectl apply → Deployment updates → cluster reconciles → done (no Terraform involvement)

4. **Cost flow** (the bill):
   - EKS control plane: $73/mo flat (always)
   - 2× t3.medium nodes: $60/mo (on-demand, or $20 with Spot)
   - RDS t4g.small: $30/mo
   - ElastiCache t4g.small: $22/mo
   - NAT Gateway: $32/mo + data
   - ALB: $17/mo + data
   - **Total: ~$235/month for staging** (~₹19,500)
   - Cut to ~$140 by using Spot nodes + dropping NAT for VPC endpoints

---

## TL;DR — what this stack represents

> A reusable, production-grade 3-tier multi-AZ AWS architecture written in ~480 lines of Terraform: VPC with 3-AZ subnet topology, ALB in public subnets routing to EKS workers in private subnets, with RDS MySQL and ElastiCache Redis isolated in the data tier. Security groups follow least-privilege chaining. Apps deploy onto the cluster via Kubernetes manifests (not Terraform). The same code, with different `terraform.tfvars`, can produce dev, staging, and prod with one-line changes.

When you can read this entire folder and trace what depends on what without referring to docs, **you have senior-engineer-level Terraform reading comprehension.**
