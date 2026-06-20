# ─────────────────────────────────────────────────────────────────────────
# Networking module — VPC + subnets + NAT + IGW + route tables + S3 endpoint
#
# Mirrors the exact topology of vpc-0f268fb3dc0dd0600 in ap-south-2.
# Designed for import (Terraform 1.5+) — resources match the live infra.
#
# After import, `terraform plan` should show:
#   Plan: 0 to add, 0 to change, 0 to destroy
#
# The import blocks live in environments/prod/imports/networking.tf
# ─────────────────────────────────────────────────────────────────────────

# ── VPC ──────────────────────────────────────────────────────────────────

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(var.tags, { Name = "fittbot-prod-vpc" })

  lifecycle {
    prevent_destroy = true
  }
}

# ── Internet Gateway ─────────────────────────────────────────────────────

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = merge(var.tags, { Name = "fittbot-prod-igw" })

  lifecycle {
    prevent_destroy = true
  }
}

# ── Subnets ──────────────────────────────────────────────────────────────

resource "aws_subnet" "public" {
  count                   = length(var.public_subnet_cidrs)
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = var.azs[count.index]
  map_public_ip_on_launch = true

  tags = merge(var.tags, {
    Name = "fittbot-prod-public-${var.azs[count.index]}"
    Tier = "public"
  })

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_subnet" "private" {
  count                   = length(var.private_subnet_cidrs)
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.private_subnet_cidrs[count.index]
  availability_zone       = var.azs[count.index]
  map_public_ip_on_launch = false

  tags = merge(var.tags, {
    Name = "fittbot-prod-private-${var.azs[count.index]}"
    Tier = "private"
  })

  lifecycle {
    prevent_destroy = true
  }
}

# ── NAT Gateway (single — already running in ap-south-2a public subnet) ──

resource "aws_eip" "nat" {
  domain = "vpc"

  tags = merge(var.tags, { Name = "fittbot-prod-nat-eip" })
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id # 2a public

  tags = merge(var.tags, { Name = "fittbot-prod-nat" })

  depends_on = [aws_internet_gateway.main]

  lifecycle {
    prevent_destroy = true
  }
}

# ── Route Tables ─────────────────────────────────────────────────────────

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = merge(var.tags, { Name = "fittbot-prod-public-rt" })
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }

  tags = merge(var.tags, { Name = "fittbot-prod-private-rt" })
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ── VPC Endpoint for S3 (Gateway type — saves NAT data charges) ──────────

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.ap-south-2.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = merge(var.tags, { Name = "fittbot-prod-s3-vpce" })
}
