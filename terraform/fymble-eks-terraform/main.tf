# Root module is just orchestration — resources live in modules/.

module "eks_cluster" {
  source = "./modules/eks-cluster"

  cluster_name                         = var.cluster_name
  kubernetes_version                   = var.kubernetes_version
  vpc_id                               = var.vpc_id
  subnet_ids                           = var.eks_subnet_ids
  endpoint_public_access               = var.cluster_endpoint_public_access
  endpoint_public_access_cidrs         = var.cluster_endpoint_public_access_cidrs
  log_types                            = var.enabled_cluster_log_types
  cloudwatch_log_retention_days        = var.cloudwatch_log_retention_days
  kms_key_deletion_window_days         = var.kms_key_deletion_window_days

  tags = local.common_tags
}




module "karpenter" {
  source = "./modules/karpenter"

  cluster_name              = var.cluster_name
  cluster_endpoint          = module.eks_cluster.cluster_endpoint
  oidc_provider_arn         = module.eks_cluster.oidc_provider_arn
  oidc_provider_url         = module.eks_cluster.cluster_oidc_issuer_url
  vpc_id                    = var.vpc_id
  subnet_ids                = var.eks_subnet_ids
  cluster_security_group_id = module.eks_cluster.cluster_security_group_id

  tags = local.common_tags
}

module "bootstrap_nodes" {
  source = "./modules/bootstrap-nodes"

  cluster_name = var.cluster_name
  subnet_ids   = var.eks_subnet_ids

  instance_types = ["t3.small"]
  desired_size   = 1
  min_size       = 1
  max_size       = 2

  tags = local.common_tags

  depends_on = [
    module.eks_cluster
  ]
}

module "karpenter_resources" {
  source = "./modules/karpenter-resources"

  cluster_name            = var.cluster_name
  karpenter_version       = "1.5.0"
  controller_iam_role_arn = module.karpenter.controller_iam_role_arn
  node_iam_role_name      = module.karpenter.node_iam_role_name
  interruption_queue_name = module.karpenter.queue_name

  instance_types = ["t3.medium", "t3.large", "t3.xlarge", "m5.large", "m5.xlarge"]
  capacity_types = ["on-demand"]
  cpu_limit      = 100

  depends_on = [
    module.bootstrap_nodes,
    module.karpenter,
  ]
}
