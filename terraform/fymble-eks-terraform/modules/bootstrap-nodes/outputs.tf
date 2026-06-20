output "node_group_name" {
  description = "Name of the bootstrap node group"
  value       = aws_eks_node_group.bootstrap.node_group_name
}

output "node_group_arn" {
  description = "ARN of the bootstrap node group"
  value       = aws_eks_node_group.bootstrap.arn
}

output "node_role_arn" {
  description = "ARN of the IAM role used by bootstrap nodes"
  value       = aws_iam_role.node.arn
}
