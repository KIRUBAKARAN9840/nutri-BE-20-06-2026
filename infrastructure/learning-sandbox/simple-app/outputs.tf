# Values exposed by this stack — useful for CI, kubectl config, etc.

output "vpc_id" {
  value       = aws_vpc.main.id
  description = "VPC ID for staging environment"
}

output "private_subnet_ids" {
  value       = aws_subnet.private[*].id
  description = "Private subnet IDs (where pods + RDS + Redis live)"
}

output "alb_dns_name" {
  value       = aws_lb.main.dns_name
  description = "ALB DNS — point Route 53 at this"
}

output "eks_cluster_name" {
  value       = aws_eks_cluster.main.name
  description = "EKS cluster name — use with `aws eks update-kubeconfig --name`"
}

output "eks_cluster_endpoint" {
  value       = aws_eks_cluster.main.endpoint
  description = "Kubernetes API endpoint"
}

output "rds_endpoint" {
  value       = aws_db_instance.main.endpoint
  description = "MySQL connection endpoint (hostname:3306)"
  sensitive   = true
}

output "redis_endpoint" {
  value       = aws_elasticache_cluster.main.cache_nodes[0].address
  description = "Redis connection endpoint"
}
