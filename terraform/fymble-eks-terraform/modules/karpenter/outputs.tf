output "controller_iam_role_arn" {
  description = "ARN of the Karpenter controller IAM role"
  value       = aws_iam_role.controller.arn
}

output "node_iam_role_arn" {
  description = "ARN of the Karpenter node IAM role"
  value       = aws_iam_role.node.arn
}

output "node_iam_role_name" {
  description = "Name of the Karpenter node IAM role (used in EC2NodeClass)"
  value       = aws_iam_role.node.name
}

output "queue_name" {
  description = "Name of the SQS queue for spot interruption events"
  value       = aws_sqs_queue.karpenter.name
}

output "queue_arn" {
  description = "ARN of the SQS queue for spot interruption events"
  value       = aws_sqs_queue.karpenter.arn
}
