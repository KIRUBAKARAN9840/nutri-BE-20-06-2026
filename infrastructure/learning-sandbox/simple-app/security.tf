# ═════════════════════════════════════════════════════════════════════
# SECURITY GROUPS — firewall rules per tier
# Principle: each tier only accepts traffic from the tier ABOVE it.
#
#   Internet → ALB SG → EKS Nodes SG → RDS SG / Redis SG
#
# ═════════════════════════════════════════════════════════════════════

# ── ALB SG: public-facing, accepts HTTP/HTTPS from anywhere ──────────
resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb-sg"
  description = "ALB: accepts HTTP/HTTPS from internet"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP from anywhere"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS from anywhere"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "All outbound (to EKS nodes)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name_prefix}-alb-sg" }
}

# ── EKS node SG: accepts traffic only from ALB SG ────────────────────
resource "aws_security_group" "eks_nodes" {
  name        = "${local.name_prefix}-eks-nodes-sg"
  description = "EKS worker nodes: traffic from ALB only"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Traffic from ALB"
    from_port       = 0
    to_port         = 65535
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id] # ◄── only ALB can reach pods
  }

  ingress {
    description = "Pod-to-pod within cluster"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true # ◄── allows this SG to talk to itself
  }

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name_prefix}-eks-nodes-sg" }
}

# ── RDS SG: accepts MySQL only from EKS nodes SG ─────────────────────
resource "aws_security_group" "rds" {
  name        = "${local.name_prefix}-rds-sg"
  description = "RDS MySQL: traffic from EKS nodes only"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "MySQL from EKS nodes"
    from_port       = 3306
    to_port         = 3306
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_nodes.id]
  }

  # No egress rule needed for RDS — it only responds, doesn't initiate.
  # AWS adds an implicit default-deny-egress when you have no rules.

  tags = { Name = "${local.name_prefix}-rds-sg" }
}

# ── Redis SG: accepts Redis port only from EKS nodes SG ──────────────
resource "aws_security_group" "redis" {
  name        = "${local.name_prefix}-redis-sg"
  description = "ElastiCache Redis: traffic from EKS nodes only"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Redis from EKS nodes"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_nodes.id]
  }

  tags = { Name = "${local.name_prefix}-redis-sg" }
}
