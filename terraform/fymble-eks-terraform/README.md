# Fymble EKS Infrastructure

Terraform configuration for Fymble's production EKS cluster.

## Layout

```
fymble-eks-terraform/
├── README.md
├── .gitignore               # blocks tfstate, tfvars, .terraform/, secrets
├── versions.tf              # pins Terraform + provider versions
├── providers.tf             # AWS provider + default tags
├── variables.tf             # input variable declarations (+ validation)
├── locals.tf                # computed values / common tags
├── data.tf                  # references to pre-existing AWS resources
├── main.tf                  # orchestration — calls modules
├── outputs.tf               # values exposed after apply
├── terraform.tfvars.example # template; real .tfvars is .gitignored
└── modules/
    └── eks-cluster/
        ├── main.tf          # EKS cluster, OIDC provider, KMS, log group
        ├── variables.tf     # module API (inputs)
        ├── outputs.tf       # module API (outputs)
        └── iam.tf           # cluster IAM role + policy attachments
```

## Security baseline (built in)

| Control | Where |
|---|---|
| EKS secrets envelope-encrypted with a customer KMS key | `modules/eks-cluster/main.tf` |
| CloudWatch log group encrypted with the same KMS key | `modules/eks-cluster/main.tf` |
| All control-plane log types enabled (api/audit/auth/cm/sched) | variable default |
| OIDC provider for IRSA (no node IAM credentials in pods) | `modules/eks-cluster/main.tf` |
| Public endpoint CIDRs forced via variable validation (no silent `0.0.0.0/0`) | `variables.tf` |
| `authentication_mode = API` (no aws-auth ConfigMap drift) | `modules/eks-cluster/main.tf` |
| State file gitignored; remote backend stanza ready to enable | `.gitignore`, `versions.tf` |
| `default_tags` on every AWS resource for ownership/cost | `providers.tf` |
| KMS key with rotation enabled + deletion window | `modules/eks-cluster/main.tf` |
| Sensitive outputs marked `sensitive = true` | `modules/eks-cluster/outputs.tf` |

## Bootstrap

```bash
cp terraform.tfvars.example terraform.tfvars   # fill in real values
terraform init
terraform plan  -out=tfplan                    # tfplan is .gitignored
terraform apply tfplan
```

## Remote state (do this before any teammate runs apply)

1. Create an S3 bucket with versioning + SSE-KMS + public-access-block.
2. Create a DynamoDB table with `LockID` (string) hash key.
3. Uncomment the `backend "s3"` block in `versions.tf` and fill it in.
4. `terraform init -migrate-state`.

Until then the state lives on whichever laptop ran `apply` — single point of failure. Do **not** share `.tfstate` files; back them up out-of-band.

## Connecting kubectl

```bash
aws eks update-kubeconfig --name <cluster_name> --region <aws_region>
```

The exact command is printed as a Terraform output.
