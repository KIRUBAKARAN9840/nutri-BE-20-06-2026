# ═════════════════════════════════════════════════════════════════════
# NETWORK TIER — VPC, public + private subnets across 3 AZs, NAT, IGW
# ═════════════════════════════════════════════════════════════════════

# ── VPC: the container for everything ────────────────────────────────
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${local.name_prefix}-vpc" }
}

# ── Internet Gateway: enables public subnets to reach the internet ───
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name_prefix}-igw" }
}

# ── Public subnets (3): host the ALB and NAT Gateway ─────────────────
# `count` creates 3 subnets, one per AZ.
resource "aws_subnet" "public" {
  count                   = length(var.public_subnet_cidrs)
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = var.azs[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name                                            = "${local.name_prefix}-public-${var.azs[count.index]}"
    Tier                                            = "public"
    # EKS needs this tag on public subnets so the AWS Load Balancer Controller
    # can discover them for internet-facing LBs:
    "kubernetes.io/role/elb"                        = "1"
    "kubernetes.io/cluster/${local.name_prefix}-eks" = "shared"
  }
}

# ── Private subnets (3): host EKS nodes, RDS, ElastiCache ────────────
resource "aws_subnet" "private" {
  count             = length(var.private_subnet_cidrs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = var.azs[count.index]

  tags = {
    Name                                             = "${local.name_prefix}-private-${var.azs[count.index]}"
    Tier                                             = "private"
    "kubernetes.io/role/internal-elb"                = "1"
    "kubernetes.io/cluster/${local.name_prefix}-eks" = "shared"
  }
}

# ── NAT Gateway: lets private subnets reach internet (outbound only) ─
# Single NAT for staging cost (one per AZ in real prod for HA).
resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "${local.name_prefix}-nat-eip" }
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id # NAT lives in first public subnet
  tags          = { Name = "${local.name_prefix}-nat" }

  depends_on = [aws_internet_gateway.main] # NAT needs IGW to exist first
}

# ── Route tables: which traffic goes where ───────────────────────────

# Public RT: default route → IGW
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${local.name_prefix}-public-rt" }
}

# Private RT: default route → NAT (so pods can reach OpenAI/Razorpay etc)
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }

  tags = { Name = "${local.name_prefix}-private-rt" }
}

# Associate each public subnet with the public RT
resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Associate each private subnet with the private RT
resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ── S3 VPC Endpoint (Gateway type) — saves NAT data charges for S3 ───
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = { Name = "${local.name_prefix}-s3-vpce" }
}
