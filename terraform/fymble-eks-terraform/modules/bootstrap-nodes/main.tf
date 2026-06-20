# EKS Managed Node Group for bootstrap workloads (CoreDNS, Karpenter)
resource "aws_eks_node_group" "bootstrap" {
  cluster_name    = var.cluster_name
  node_group_name = "bootstrap"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = var.subnet_ids

  instance_types = var.instance_types
  capacity_type  = "ON_DEMAND"

  scaling_config {
    desired_size = var.desired_size
    min_size     = var.min_size
    max_size     = var.max_size
  }

  update_config {
    max_unavailable = 1
  }

  # Taint these nodes so only system pods run here
  # Applications use Karpenter-provisioned nodes
  taint {
    key    = "CriticalAddonsOnly"
    value  = "true"
    effect = "NO_SCHEDULE"
  }

  labels = {
    "role" = "bootstrap"
  }

  tags = merge(var.tags, {
    Name = "${var.cluster_name}-bootstrap"
  })

  # Ensure IAM policies are attached before creating
  depends_on = [
    aws_iam_role_policy_attachment.worker,
    aws_iam_role_policy_attachment.cni,
    aws_iam_role_policy_attachment.ecr,
    aws_iam_role_policy_attachment.ssm,
  ]

  # Don't recreate nodes for things like desired_size changes
  lifecycle {
    ignore_changes = [scaling_config[0].desired_size]
  }
}
