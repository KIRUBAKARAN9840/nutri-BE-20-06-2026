resource "aws_vpc" "learning" {
  cidr_block           = "10.99.0.0/16"
  enable_dns_hostnames = true

  tags = {
    Name = "fittbot-tf-learning-vpc"
  }
}

resource "aws_security_group" "web" {
  name        = "fittbot-tf-learning-web-sg"
  description = "Allow HTTPS in, anything out"
  vpc_id      = aws_vpc.learning.id

  ingress {
    description = "HTTPS from anywhere"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "db" {
  name        = "fittbot-tf-learning-db-sg"
  description = "Allow MySQL only from the web tier"
  vpc_id      = aws_vpc.learning.id
  
  ingress {
    description     = "MySQL from web SG only"
    from_port       = 3306
    to_port         = 3306
    protocol        = "tcp"
    security_groups = [aws_security_group.web.id]
  }

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
