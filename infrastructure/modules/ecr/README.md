# ECR module — STUB

Two repositories:
- `fittbot/backend`
- `fittbot/paymentgateway`

## Add this lifecycle policy (cost win)

Currently no lifecycle policy → old image layers accumulate. Add:

```hcl
resource "aws_ecr_lifecycle_policy" "expire_untagged" {
  for_each   = toset(var.repositories)
  repository = each.value

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire untagged images older than 30 days"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 30
      }
      action = { type = "expire" }
    }]
  })
}
```
