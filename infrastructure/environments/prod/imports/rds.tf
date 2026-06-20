# ─────────────────────────────────────────────────────────────────────────
# Import blocks — RDS MySQL `devfittbotdb`
#
# Phase 3.7. db.t3.medium, MySQL 8.0.44, single-AZ in 2a, gp2, encrypted.
# ─────────────────────────────────────────────────────────────────────────

# import {
#   to = module.rds_mysql.aws_db_instance.main
#   id = "devfittbotdb"
# }
#
# # The DB subnet group is named:
# import {
#   to = module.rds_mysql.aws_db_subnet_group.main
#   id = "devfittbotdb-subnet-group"
# }
#
# # The DB parameter group is the AWS default — usually you cannot import that;
# # plan to create a custom one (e.g., fittbot-prod-mysql8) and switch the
# # instance to use it. Don't import default.mysql8.0.
#
# # Security group attached to RDS:
# # import {
# #   to = module.rds_mysql.aws_security_group.rds
# #   id = "sg-002d102fa9037d7ed"
# # }
