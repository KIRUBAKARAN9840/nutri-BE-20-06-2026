# Terraform State Backend Bootstrap

## What this does

Creates **two AWS resources** that store and lock Terraform state:

1. **S3 bucket** `fittbot-tfstate-ap-south-2` — holds `.tfstate` files for every environment
2. **DynamoDB table** `terraform-state-lock` — prevents two engineers from running `terraform apply` simultaneously

## Why it's separate from the rest

Chicken-and-egg: every Terraform config needs a state backend, but the backend itself is created by Terraform. Solution: this folder uses **local state** (the default) — once it runs, all other folders use the S3 backend it created.

## Cost

| Resource | Monthly cost |
|---|---|
| S3 bucket (state files are tiny, ~10 KB each) | < $0.01 |
| DynamoDB on-demand (idle = $0) | < $0.05 |
| **Total** | **< $0.10/month** |

## Run it

```bash
cd infrastructure/global/backend-bootstrap

terraform init     # downloads AWS provider
terraform plan     # MUST show: 1 bucket + 1 table + supporting resources to ADD
                   # MUST NOT show any "destroy" or "change"
terraform apply    # creates the resources (creating new = safe, not destructive)

# Verify the bucket exists
aws s3api head-bucket --bucket fittbot-tfstate-ap-south-2 --region ap-south-2

# Verify the lock table exists
aws dynamodb describe-table --region ap-south-2 \
  --table-name terraform-state-lock \
  --query 'Table.TableStatus' --output text
```

## After running

- A local `terraform.tfstate` file is created in this folder. **Don't delete it** — you'd need it to ever change/destroy these resources.
- Commit the `.tf` files. **Do NOT commit `terraform.tfstate`** (it's in `.gitignore`).
- `lifecycle { prevent_destroy = true }` is set on both resources — Terraform will refuse to delete them.

## Never run this again

After bootstrap, you should never need to `terraform apply` here. Only re-run if:
- You add a new region (add S3+lock for that region)
- You want to enable cross-region replication on the state bucket

Both are big decisions — discuss before running.
