# ─────────────────────────────────────────────────────────────────────────
# Import blocks — ECS Cluster + 7 services
#
# Phases 3.9 and 3.10.
# ─────────────────────────────────────────────────────────────────────────

# # Cluster
# import {
#   to = module.ecs_cluster.aws_ecs_cluster.main
#   id = "dev-codedeploy-cluster-test"
# }
#
# # ──── Fittbot-Production (FastAPI, 2 tasks) ────
# import {
#   to = module.ecs_service_api.aws_ecs_service.this
#   id = "dev-codedeploy-cluster-test/Fittbot-Production"
# }
# import {
#   to = module.ecs_service_api.aws_ecs_task_definition.this
#   id = "Fittbot-Production:188"
# }
#
# # ──── Celery-Payment_Queue ────
# import {
#   to = module.ecs_service_celery_payments.aws_ecs_service.this
#   id = "dev-codedeploy-cluster-test/Celery-Payment_Queue"
# }
# import {
#   to = module.ecs_service_celery_payments.aws_ecs_task_definition.this
#   id = "celery_production_task_payment_queues:41"
# }
#
# # ──── celery_production_service (ai + chat_diet + default queues) ────
# import {
#   to = module.ecs_service_celery_general.aws_ecs_service.this
#   id = "dev-codedeploy-cluster-test/celery_production_service"
# }
# import {
#   to = module.ecs_service_celery_general.aws_ecs_task_definition.this
#   id = "celery_production_task:33"
# }
#
# # ──── Fittbot-AI-DietCoach (gevent worker) ────
# import {
#   to = module.ecs_service_ai_diet.aws_ecs_service.this
#   id = "dev-codedeploy-cluster-test/Fittbot-AI-DietCoach"
# }
# import {
#   to = module.ecs_service_ai_diet.aws_ecs_task_definition.this
#   id = "AI-dietcoach:4"
# }
#
# # ──── Client-Tracking-service ────
# import {
#   to = module.ecs_service_client_tracking.aws_ecs_service.this
#   id = "dev-codedeploy-cluster-test/Client-Tracking-service"
# }
# import {
#   to = module.ecs_service_client_tracking.aws_ecs_task_definition.this
#   id = "Client-Tracking:2"
# }
#
# # ──── reminder-service (SQS poller, 30s) ────
# import {
#   to = module.ecs_service_reminder.aws_ecs_service.this
#   id = "dev-codedeploy-cluster-test/reminder-service"
# }
# import {
#   to = module.ecs_service_reminder.aws_ecs_task_definition.this
#   id = "reminder:10"
# }
#
# # ──── fittbot-pg-service (Apple-PG frontend) ────
# import {
#   to = module.ecs_service_pg.aws_ecs_service.this
#   id = "dev-codedeploy-cluster-test/fittbot-pg-service"
# }
# import {
#   to = module.ecs_service_pg.aws_ecs_task_definition.this
#   id = "fittbot-pg:15"
# }
