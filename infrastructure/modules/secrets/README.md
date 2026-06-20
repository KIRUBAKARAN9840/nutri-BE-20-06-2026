# Secrets Manager module — STUB

Imports the 7 secrets. **Only imports metadata**; secret values are never written to Terraform state.

```hcl
resource "aws_secretsmanager_secret" "fittbot_main" {
  name        = "fittbot/secrets"
  description = "Main app secrets — DB password, API keys, JWT secrets"
  kms_key_id  = aws_kms_key.secrets.id

  recovery_window_in_days = 30

  lifecycle {
    prevent_destroy = true
  }
}

# Versions are NOT in Terraform — app reads them at runtime
```

## Why values stay out

If we tracked `aws_secretsmanager_secret_version` resources with values, those values land in tfstate (encrypted, but readable to anyone with state access). Better: only import the secret container; rotate values via the AWS console or a dedicated rotation Lambda.
