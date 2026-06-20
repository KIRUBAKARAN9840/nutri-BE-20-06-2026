# ---------------------------------------------------------------------------
# Karpenter Controller IAM Role (assumed by Karpenter pods via IRSA)
# ---------------------------------------------------------------------------

# Trust policy — only Karpenter ServiceAccount can assume this role
data "aws_iam_policy_document" "controller_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    effect  = "Allow"

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${replace(var.oidc_provider_url, "https://", "")}:sub"
      values   = ["system:serviceaccount:karpenter:karpenter"]
    }

    condition {
      test     = "StringEquals"
      variable = "${replace(var.oidc_provider_url, "https://", "")}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "controller" {
  name               = "${var.cluster_name}-karpenter-controller"
  assume_role_policy = data.aws_iam_policy_document.controller_trust.json

  tags = merge(var.tags, {
    Name = "${var.cluster_name}-karpenter-controller"
  })
}

# Inline policy: what Karpenter is allowed to do in AWS
data "aws_iam_policy_document" "controller_perms" {
  # Manage EC2 instances (create, describe, terminate)
  statement {
    actions = [
      "ec2:CreateLaunchTemplate",
      "ec2:CreateFleet",
      "ec2:RunInstances",
      "ec2:CreateTags",
      "ec2:TerminateInstances",
      "ec2:DescribeInstances",
      "ec2:DescribeImages",
      "ec2:DescribeInstanceTypes",
      "ec2:DescribeInstanceTypeOfferings",
      "ec2:DescribeAvailabilityZones",
      "ec2:DescribeLaunchTemplates",
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeSubnets",
      "ec2:DescribeSpotPriceHistory",
      "ec2:DeleteLaunchTemplate",
      "pricing:GetProducts",
      "ssm:GetParameter",
    ]
    resources = ["*"]
  }

  # Pass the node IAM role to EC2 instances Karpenter creates
  statement {
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.node.arn]
  }

  # Read EKS cluster info
  statement {
    actions = [
      "eks:DescribeCluster",
    ]
    resources = ["arn:aws:eks:*:*:cluster/${var.cluster_name}"]
  }

  # SQS for spot interruption handling
  statement {
    actions = [
      "sqs:DeleteMessage",
      "sqs:GetQueueUrl",
      "sqs:ReceiveMessage",
    ]
    resources = [aws_sqs_queue.karpenter.arn]
  }

  # Create instance profile for nodes (needed for Karpenter v1+)
  statement {
    actions = [
      "iam:CreateInstanceProfile",
      "iam:DeleteInstanceProfile",
      "iam:GetInstanceProfile",
      "iam:AddRoleToInstanceProfile",
      "iam:RemoveRoleFromInstanceProfile",
      "iam:TagInstanceProfile",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "controller" {
  name   = "karpenter-controller-policy"
  role   = aws_iam_role.controller.id
  policy = data.aws_iam_policy_document.controller_perms.json
}
