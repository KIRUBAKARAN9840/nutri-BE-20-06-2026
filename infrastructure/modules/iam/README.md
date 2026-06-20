# IAM module — STUB

For KMS keys (4 customer-managed) and ECS task/execution roles. IAM is global, not regional, so be careful about cross-region implications.

Customer-managed KMS keys to import:
- `alias/fittbot/dbcredentials` → `bba3b9c1-9280-41f3-9da4-bf30dea8baa2`
- `alias/fittbot/mysqldb` → `309b5807-7f16-40d4-9bfb-fc923f1b0fcc`
- `alias/fittbot/otpsecrets` → `394d6e5f-6d69-4de5-8ad8-44221805820f`
- `alias/fittbot/sessiontoken` → `010c4386-b00a-4b7a-87b4-4089cdcfeb53`

## `prevent_destroy` is mandatory on KMS keys

Deleting a KMS key is **irreversible after the deletion window**. If an app encrypts data with a key and then the key is deleted, that data is permanently unreadable.

```hcl
resource "aws_kms_key" "mysqldb" {
  # ... config ...
  deletion_window_in_days = 30   # max delay before actual delete

  lifecycle {
    prevent_destroy = true
  }
}
```
