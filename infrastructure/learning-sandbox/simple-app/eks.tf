# ═════════════════════════════════════════════════════════════════════
# EKS TIER — Kubernetes cluster + managed node group
#
# Three groups of resources:
#  1. IAM roles for the cluster control plane + worker nodes
#  2. The EKS cluster itself
#  3. A managed node group (EC2 instances that run pods)
#
# Apps are deployed AS PODS via kubectl/Helm/ArgoCD — NOT via Terraform.
# Terraform's job is to provision the cluster + nodes; what runs on top
# is a separate concern.
# ═════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────
# 1. IAM ROLES
# ─────────────────────────────────────────────────────────────────────

# ── Cluster role: lets EKS service create AWS resources for the cluster
resource "aws_iam_role" "eks_cluster" {
  name = "${local.name_prefix}-eks-cluster-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = { Name = "${local.name_prefix}-eks-cluster-role" }
}

resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
  role       = aws_iam_role.eks_cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

# ── Node role: lets worker EC2 instances pull from ECR, write CW logs,
#    and talk to the EKS API.
resource "aws_iam_role" "eks_nodes" {
  name = "${local.name_prefix}-eks-nodes-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = { Name = "${local.name_prefix}-eks-nodes-role" }
}

resource "aws_iam_role_policy_attachment" "eks_worker_node" {
  role       = aws_iam_role.eks_nodes.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "eks_cni" {
  role       = aws_iam_role.eks_nodes.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy_attachment" "ecr_readonly" {
  role       = aws_iam_role.eks_nodes.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

# ─────────────────────────────────────────────────────────────────────
# 2. THE EKS CLUSTER
# ─────────────────────────────────────────────────────────────────────

resource "aws_eks_cluster" "main" {
  name     = "${local.name_prefix}-eks"
  role_arn = aws_iam_role.eks_cluster.arn
  version  = "1.29" # pin Kubernetes version

  vpc_config {
    # Cluster control plane ENIs land in BOTH private AND public subnets
    # so AWS can route traffic. The pods run in private subnets only.
    subnet_ids              = concat(aws_subnet.private[*].id, aws_subnet.public[*].id)
    endpoint_private_access = true  # kubectl from inside the VPC works
    endpoint_public_access  = true  # kubectl from your laptop works (lock down for prod)
    public_access_cidrs     = ["0.0.0.0/0"] # restrict to office IPs in prod
  }

  enabled_cluster_log_types = ["api", "audit", "authenticator"]

  # Cluster needs the IAM policy attached before AWS will create it
  depends_on = [aws_iam_role_policy_attachment.eks_cluster_policy]

  tags = { Name = "${local.name_prefix}-eks" }
}

# ─────────────────────────────────────────────────────────────────────
# 3. MANAGED NODE GROUP — EC2 instances that run the actual pods
# ─────────────────────────────────────────────────────────────────────
#
# This is where your services run. Each service (api, celery-payments,
# celery-general, etc.) becomes a Kubernetes Deployment of pods,
# scheduled by the EKS control plane onto these nodes.
#
# Multi-AZ is achieved automatically because we pass 3 private subnets
# across 3 AZs.

resource "aws_eks_node_group" "main" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "${local.name_prefix}-nodes"
  node_role_arn   = aws_iam_role.eks_nodes.arn
  subnet_ids      = aws_subnet.private[*].id # nodes only in private subnets

  instance_types = ["t3.medium"] # 2 vCPU / 4 GB — fine for staging
  capacity_type  = "ON_DEMAND"   # use "SPOT" for cost savings on staging

  scaling_config {
    desired_size = 2  # start with 2 nodes
    min_size     = 2  # never go below 2 (HA across AZs)
    max_size     = 5  # autoscale up to 5 under load
  }

  update_config {
    max_unavailable = 1 # during rolling updates, max 1 node down at a time
  }

  labels = {
    Environment = var.environment
    Workload    = "general"
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_worker_node,
    aws_iam_role_policy_attachment.eks_cni,
    aws_iam_role_policy_attachment.ecr_readonly,
  ]

  tags = { Name = "${local.name_prefix}-nodes" }
}
