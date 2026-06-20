# Observability module — STUB

Will manage:

- 110 CloudWatch log groups (mostly with `retention_in_days = null` today — fix this!)
- CloudWatch alarms (none exist today — create them)
- Container Insights settings

## Critical alarms to define

```hcl
resource "aws_cloudwatch_metric_alarm" "rds_low_memory" {
  alarm_name          = "fittbot-rds-low-memory"
  metric_name         = "FreeableMemory"
  namespace           = "AWS/RDS"
  dimensions          = { DBInstanceIdentifier = "devfittbotdb" }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 314572800   # 300 MB
  comparison_operator = "LessThanThreshold"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# Similar alarms for:
#  - RDS CPU > 80 %
#  - Redis BytesUsedForCache > 80 %
#  - ALB 5xx > 10/min
#  - Lambda errors > 5/min
#  - ECS service Running != Desired
```

## Log retention enforcement

```hcl
resource "aws_cloudwatch_log_group" "ecs_logs" {
  for_each          = toset(var.log_group_names)
  name              = each.value
  retention_in_days = 7
}
```

This single block, once imported, will set retention=7 on all 108 log groups currently set to `null`. Big cost saver.
