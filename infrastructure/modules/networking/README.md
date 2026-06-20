# Networking module

Mirrors `vpc-0f268fb3dc0dd0600` exactly:

- 1 VPC (10.0.0.0/16)
- 1 IGW
- 6 subnets (3 public, 3 private across 2a / 2b / 2c)
- 1 NAT Gateway (in 2a public subnet, EIP 18.60.96.58)
- 1 public route table (default route → IGW)
- 1 private route table (default route → NAT + S3 VPCE)
- 1 S3 Gateway VPC endpoint

## Why import this first

- **Lowest blast radius** — even if the import goes wrong, you can't break running compute
- **Most dependencies** — almost every other resource references VPC/subnet IDs, so getting these right first makes downstream imports easier
- **Most stable** — these resources don't change often

## Import block sequence

See `environments/prod/imports/networking.tf` for the actual `import { ... }` blocks.

## After import

`terraform plan` should print:

```
No changes. Your infrastructure matches the configuration.
```

If it shows ANY diff, fix the HCL — don't apply.
