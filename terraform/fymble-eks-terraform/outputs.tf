output "cluster_name" {
  description = "EKS cluster name."
  value       = module.eks_cluster.cluster_name
}

output "cluster_endpoint" {
  description = "EKS API server endpoint."
  value       = module.eks_cluster.cluster_endpoint
}

output "cluster_certificate_authority" {
  description = "Base64-encoded cluster CA. Used by kubeconfig."
  value       = module.eks_cluster.cluster_certificate_authority
  sensitive   = true
}

output "cluster_oidc_issuer_url" {
  description = "OIDC issuer URL — used by IRSA."
  value       = module.eks_cluster.cluster_oidc_issuer_url
}

output "oidc_provider_arn" {
  description = "ARN of the IAM OIDC provider for IRSA."
  value       = module.eks_cluster.oidc_provider_arn
}

output "cluster_security_group_id" {
  description = "Cluster security group created by EKS."
  value       = module.eks_cluster.cluster_security_group_id
}

output "cluster_kms_key_arn" {
  description = "KMS key encrypting Kubernetes secrets and control-plane logs."
  value       = module.eks_cluster.cluster_kms_key_arn
}

output "kubeconfig_command" {
  description = "Run this to point kubectl at the new cluster."
  value       = "aws eks update-kubeconfig --name ${module.eks_cluster.cluster_name} --region ${var.aws_region}"
}
