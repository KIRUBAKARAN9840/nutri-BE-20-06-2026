# ALB module — STUB

Will mirror `dev-lb-new`. Includes 14 target groups (many are blue/green leftovers).

## Resources

- `aws_lb.main` — the ALB
- `aws_lb_listener.https` — 443 listener
- `aws_lb_target_group.*` — 14 target groups
- `aws_lb_listener_rule.*` — host-based + path-based routes

## Cleanup opportunity

Only ~3 target groups are actively used (`dev-blue-target-group-new`, `paymentgateway-tg`, `admin-app`). The rest are blue/green leftovers from earlier deploys:

```
blue-dig-tg, green-dig-tg, dev-green-target-group-new,
local-blue-target-group, local-green-target-group,
new-dev, prom-tg-1, prom-tg-2, staging-fastapi-tg
```

Plan: import only the **active** TGs into Terraform; the orphans can be deleted via console after a 7-day cooldown to ensure nothing references them.
