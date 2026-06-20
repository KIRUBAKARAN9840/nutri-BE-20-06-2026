# ─────────────────────────────────────────────────────────────────────────
# Import blocks — ALB `dev-lb-new` + target groups + listeners
#
# Phase 3.5. 14 target groups exist (most are blue/green leftovers).
# Import the ALB + active TGs; consider deleting orphans separately.
# ─────────────────────────────────────────────────────────────────────────

# # ALB
# import {
#   to = module.alb.aws_lb.main
#   id = "arn:aws:elasticloadbalancing:ap-south-2:182399696098:loadbalancer/app/dev-lb-new/a6922d9f8a33b357"
# }
#
# # Listener (HTTPS 443)
# import {
#   to = module.alb.aws_lb_listener.https
#   id = "<listener-arn>"   # get from: aws elbv2 describe-listeners --load-balancer-arn ...
# }
#
# # Active target groups (the ones still in use)
# import {
#   to = module.alb.aws_lb_target_group.api  # main FastAPI on port 8000
#   id = "<dev-blue-target-group-new ARN>"
# }
# import {
#   to = module.alb.aws_lb_target_group.payment_gateway
#   id = "<paymentgateway-tg ARN>"
# }
# import {
#   to = module.alb.aws_lb_target_group.admin_app
#   id = "<admin-app ARN>"
# }
#
# # Listener rules
# import {
#   to = module.alb.aws_lb_listener_rule.payments_host
#   id = "<rule-arn-priority-100>"
# }
# import {
#   to = module.alb.aws_lb_listener_rule.admin_host
#   id = "<rule-arn-priority-150>"
# }
# import {
#   to = module.alb.aws_lb_listener_rule.green_path
#   id = "<rule-arn-priority-210>"
# }
#
# # Get TG and listener-rule ARNs with:
# # aws elbv2 describe-target-groups --names dev-blue-target-group-new
# # aws elbv2 describe-rules --listener-arn <listener-arn>
