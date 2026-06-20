# WAF module — STUB

Regional WAFv2 Web ACL attached to the ALB.

⚠️ Current IAM user `fittbot-aws-cli` **cannot list WAF rules**. Attach `ReadOnlyAccess` first (see `RUNBOOK.md` Phase 0b), then enumerate:

```bash
aws wafv2 list-web-acls --scope REGIONAL --region ap-south-2
aws wafv2 get-web-acl --scope REGIONAL --region ap-south-2 \
  --id <id> --name <name>
```

Then write the matching `aws_wafv2_web_acl` resource.

## Cost optimization

The audit suggested dropping WAF for ~$55/mo savings. If you do that, replace with Cloudflare proxy + your existing FastAPI rate limiting. **Don't do this as part of the import** — separate PR after backfill.
