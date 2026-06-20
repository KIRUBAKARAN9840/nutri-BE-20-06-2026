# ═════════════════════════════════════════════════════════════════════
# DATA TIER — RDS MySQL in private subnets
# ═════════════════════════════════════════════════════════════════════

# DB subnet group: tells RDS which subnets it can place the instance in.
# Must include subnets in at least 2 AZs (RDS requirement) even for single-AZ.
resource "aws_db_subnet_group" "main" {
  name       = "${local.name_prefix}-rds-subnet-group"
  subnet_ids = aws_subnet.private[*].id # all 3 private subnets

  tags = { Name = "${local.name_prefix}-rds-subnet-group" }
}

resource "aws_db_instance" "main" {
  identifier             = "${local.name_prefix}-mysql"
  engine                 = "mysql"
  engine_version         = "8.0.44"
  instance_class         = "db.t4g.small" # 2 vCPU, 2 GB — staging size
  allocated_storage      = 20
  storage_type           = "gp3"
  storage_encrypted      = true

  db_name                = "fittbot"
  username               = "admin"
  password               = var.rds_password # passed via TF_VAR_rds_password env var

  vpc_security_group_ids = [aws_security_group.rds.id]
  db_subnet_group_name   = aws_db_subnet_group.main.name
  publicly_accessible    = false

  multi_az               = false # staging — set true for prod (+100% cost)
  backup_retention_period = 7
  backup_window          = "21:30-22:00"      # UTC — 3:00 AM IST
  maintenance_window     = "sun:22:30-sun:23:30" # UTC — Sun 4:00 AM IST

  skip_final_snapshot       = true  # staging only — NEVER true for prod
  deletion_protection       = false # staging only — true for prod

  tags = { Name = "${local.name_prefix}-mysql" }
}
