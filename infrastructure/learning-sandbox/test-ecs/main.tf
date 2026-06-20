resource "aws_ecs_cluster" "learning" {
  name = "fittbot-tf-learning-cluster"
}

resource "aws_ecs_task_definition" "api" {
  family                   = "fittbot-tf-learning-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"   # 0.5 vCPU (Fargate uses string values)
  memory                   = "1024"  # 1 GB

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = "182399696098.dkr.ecr.ap-south-2.amazonaws.com/fittbot/backend:latest"
      essential = true
      portMappings = [
        { containerPort = 8000, protocol = "tcp" }
      ]
    }
  ])
}

resource "aws_ecs_service" "api" {
  name            = "fittbot-tf-learning-api"
  cluster         = aws_ecs_cluster.learning.id
  task_definition = aws_ecs_task_definition.api.arn   # ◄── references the task def's CURRENT revision
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = ["subnet-XXXX", "subnet-YYYY"]  # placeholders
    security_groups  = ["sg-XXXX"]
    assign_public_ip = false
  }
}
