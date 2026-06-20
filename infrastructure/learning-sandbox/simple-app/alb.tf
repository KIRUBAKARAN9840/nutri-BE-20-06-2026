# ═════════════════════════════════════════════════════════════════════
# ALB TIER — Application Load Balancer in public subnets
#
# Routes traffic from internet → EKS nodes via target group.
# In real EKS deploys, the AWS Load Balancer Controller creates ALBs
# automatically from Kubernetes Ingress resources — but for staging
# we create one explicit ALB to learn the resource type.
# ═════════════════════════════════════════════════════════════════════

resource "aws_lb" "main" {
  name               = "${local.name_prefix}-alb"
  internal           = false               # ◄── internet-facing
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id # ◄── all 3 public subnets

  enable_deletion_protection = false # staging — turn ON for prod

  tags = { Name = "${local.name_prefix}-alb" }
}

# Target group — where the ALB forwards traffic.
# In EKS, pods register here via the AWS Load Balancer Controller.
resource "aws_lb_target_group" "app" {
  name        = "${local.name_prefix}-app-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip" # required for EKS Fargate / awsvpc mode

  health_check {
    enabled             = true
    path                = "/health"
    port                = "traffic-port"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    matcher             = "200"
  }

  tags = { Name = "${local.name_prefix}-app-tg" }
}

# HTTP listener on port 80
# For prod, you'd add a 443 listener with an ACM certificate.
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}
