provider "aws" {
  region = var.region
}

# ============================================
# SNS Topic + Email Subscriptions
# ============================================

resource "aws_sns_topic" "fittbot_alerts" {
  name = "fittbot-alerts"
}

resource "aws_sns_topic_subscription" "naveen" {
  topic_arn = aws_sns_topic.fittbot_alerts.arn
  protocol  = "email"
  endpoint  = "naveenkulandasamy@gmail.com"
}

resource "aws_sns_topic_subscription" "martin" {
  topic_arn = aws_sns_topic.fittbot_alerts.arn
  protocol  = "email"
  endpoint  = "martin@fymble.app"
}

# ============================================
# ECS Alarms
# ============================================

resource "aws_cloudwatch_metric_alarm" "ecs_cpu_high" {
  alarm_name          = "fittbot-ecs-cpu-high"
  alarm_description   = "ECS CPU utilization above 75%"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = 180
  statistic           = "Average"
  threshold           = 75
  alarm_actions       = [aws_sns_topic.fittbot_alerts.arn]
  ok_actions          = [aws_sns_topic.fittbot_alerts.arn]

  dimensions = {
    ClusterName = var.ecs_cluster_name
    ServiceName = var.ecs_service_name
  }
}

resource "aws_cloudwatch_metric_alarm" "ecs_memory_high" {
  alarm_name          = "fittbot-ecs-memory-high"
  alarm_description   = "ECS memory utilization above 85%"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "MemoryUtilization"
  namespace           = "AWS/ECS"
  period              = 180
  statistic           = "Average"
  threshold           = 85
  alarm_actions       = [aws_sns_topic.fittbot_alerts.arn]
  ok_actions          = [aws_sns_topic.fittbot_alerts.arn]

  dimensions = {
    ClusterName = var.ecs_cluster_name
    ServiceName = var.ecs_service_name
  }
}

# ============================================
# RDS Alarms
# ============================================

resource "aws_cloudwatch_metric_alarm" "rds_cpu_high" {
  alarm_name          = "fittbot-rds-cpu-high"
  alarm_description   = "RDS CPU utilization above 80%"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  alarm_actions       = [aws_sns_topic.fittbot_alerts.arn]
  ok_actions          = [aws_sns_topic.fittbot_alerts.arn]

  dimensions = {
    DBInstanceIdentifier = var.rds_instance_id
  }
}

resource "aws_cloudwatch_metric_alarm" "rds_cpu_credits_low" {
  alarm_name          = "fittbot-rds-cpu-credits-low"
  alarm_description   = "CRITICAL: RDS CPU credits below 30 - DB will throttle"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "CPUCreditBalance"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 30
  alarm_actions       = [aws_sns_topic.fittbot_alerts.arn]
  ok_actions          = [aws_sns_topic.fittbot_alerts.arn]

  dimensions = {
    DBInstanceIdentifier = var.rds_instance_id
  }
}

resource "aws_cloudwatch_metric_alarm" "rds_connections_high" {
  alarm_name          = "fittbot-rds-connections-high"
  alarm_description   = "RDS connections above 200"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "DatabaseConnections"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 200
  alarm_actions       = [aws_sns_topic.fittbot_alerts.arn]
  ok_actions          = [aws_sns_topic.fittbot_alerts.arn]

  dimensions = {
    DBInstanceIdentifier = var.rds_instance_id
  }
}

# ============================================
# ElastiCache (Redis) Alarms
# ============================================

resource "aws_cloudwatch_metric_alarm" "redis_memory_high" {
  alarm_name          = "fittbot-redis-memory-high"
  alarm_description   = "Redis memory usage above 70%"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "DatabaseMemoryUsagePercentage"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 70
  alarm_actions       = [aws_sns_topic.fittbot_alerts.arn]
  ok_actions          = [aws_sns_topic.fittbot_alerts.arn]

  dimensions = {
    CacheClusterId = var.elasticache_cluster_id
  }
}

resource "aws_cloudwatch_metric_alarm" "redis_connections_high" {
  alarm_name          = "fittbot-redis-connections-high"
  alarm_description   = "Redis connections above 800"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "CurrConnections"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 800
  alarm_actions       = [aws_sns_topic.fittbot_alerts.arn]
  ok_actions          = [aws_sns_topic.fittbot_alerts.arn]

  dimensions = {
    CacheClusterId = var.elasticache_cluster_id
  }
}

# ============================================
# ALB Alarms
# ============================================

resource "aws_cloudwatch_metric_alarm" "alb_5xx_errors" {
  alarm_name          = "fittbot-alb-5xx-errors"
  alarm_description   = "CRITICAL: ALB 5xx errors above 50 in 5 minutes"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "HTTPCode_ELB_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  statistic           = "Sum"
  threshold           = 50
  alarm_actions       = [aws_sns_topic.fittbot_alerts.arn]
  ok_actions          = [aws_sns_topic.fittbot_alerts.arn]
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
  }
}

resource "aws_cloudwatch_metric_alarm" "alb_target_5xx_errors" {
  alarm_name          = "fittbot-alb-target-5xx-errors"
  alarm_description   = "CRITICAL: Target (ECS) 5xx errors above 50 in 5 minutes"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  statistic           = "Sum"
  threshold           = 50
  alarm_actions       = [aws_sns_topic.fittbot_alerts.arn]
  ok_actions          = [aws_sns_topic.fittbot_alerts.arn]
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = var.target_group_arn_suffix
  }
}

resource "aws_cloudwatch_metric_alarm" "alb_response_time" {
  alarm_name          = "fittbot-alb-response-time-high"
  alarm_description   = "ALB target response time above 2 seconds (p95)"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  extended_statistic  = "p95"
  threshold           = 2
  alarm_actions       = [aws_sns_topic.fittbot_alerts.arn]
  ok_actions          = [aws_sns_topic.fittbot_alerts.arn]

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
  }
}

resource "aws_cloudwatch_metric_alarm" "alb_unhealthy_hosts" {
  alarm_name          = "fittbot-alb-unhealthy-hosts"
  alarm_description   = "CRITICAL: Unhealthy targets detected"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "UnHealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  alarm_actions       = [aws_sns_topic.fittbot_alerts.arn]
  ok_actions          = [aws_sns_topic.fittbot_alerts.arn]

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = var.target_group_arn_suffix
  }
}
