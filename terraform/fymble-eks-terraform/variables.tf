# =============================================================================
# Input variables — the public API of this root module.
# Defaults are safe-by-default; sensitive values come from terraform.tfvars.
# =============================================================================

variable "aws_region" {
  description = "AWS region where the EKS cluster will live."
  type        = string
  default     = "ap-south-2"
}

variable "environment" {
  description = "Environment name (prod, staging, dev). Used in tags."
  type        = string
  default     = "prod"

  validation {
    condition     = contains(["prod", "staging", "dev"], var.environment)
    error_message = "environment must be one of: prod, staging, dev."
  }
}

variable "owner" {
  description = "Team or individual responsible for the cluster (tag value)."
  type        = string
  default     = "platform"
}

variable "cluster_name" {
  description = "Name of the EKS cluster. Must be unique within the account/region."
  type        = string
  default     = "fymble-prod"

  validation {
    condition     = can(regex("^[a-zA-Z][a-zA-Z0-9-]{0,99}$", var.cluster_name))
    error_message = "cluster_name must start with a letter and contain only alphanumerics or hyphens (max 100 chars)."
  }
}

variable "kubernetes_version" {
  description = "Kubernetes minor version for the EKS control plane."
  type        = string
  default     = "1.34"

  validation {
    condition     = can(regex("^1\\.(3[3-9]|[4-9][0-9])$", var.kubernetes_version))
    error_message = "kubernetes_version must be 1.33 or higher (e.g. 1.34, 1.35)."
  }
}

variable "vpc_id" {
  description = "ID of the pre-existing VPC the cluster runs in."
  type        = string

  validation {
    condition     = can(regex("^vpc-[a-f0-9]{8,17}$", var.vpc_id))
    error_message = "vpc_id must look like 'vpc-xxxxxxxx'."
  }
}

variable "eks_subnet_ids" {
  description = "Private subnet IDs for the EKS control plane ENIs. Minimum 2 AZs."
  type        = list(string)

  validation {
    condition     = length(var.eks_subnet_ids) >= 2
    error_message = "Provide at least 2 subnets in different AZs (EKS HA requirement)."
  }
}

variable "cluster_endpoint_public_access" {
  description = "Whether the EKS API server is reachable over the public internet."
  type        = bool
  default     = true
}

variable "cluster_endpoint_public_access_cidrs" {
  description = "CIDR blocks allowed to reach the public EKS API endpoint. NEVER leave as 0.0.0.0/0 in prod."
  type        = list(string)
  default     = []

  validation {
    condition     = length(var.cluster_endpoint_public_access_cidrs) > 0
    error_message = "You must explicitly list allowed CIDRs (e.g. office IP /32). Open access (0.0.0.0/0) requires opting in deliberately."
  }
}

variable "enabled_cluster_log_types" {
  description = "Control-plane log types shipped to CloudWatch. Keep all five enabled for audit."
  type        = list(string)
  default     = ["api", "audit", "authenticator", "controllerManager", "scheduler"]
}

variable "cloudwatch_log_retention_days" {
  description = "How long control-plane logs are retained in CloudWatch."
  type        = number
  default     = 90

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, 3653], var.cloudwatch_log_retention_days)
    error_message = "cloudwatch_log_retention_days must be one of the AWS-allowed retention values."
  }
}

variable "kms_key_deletion_window_days" {
  description = "Pending-deletion window for the cluster KMS key (7–30 days)."
  type        = number
  default     = 30

  validation {
    condition     = var.kms_key_deletion_window_days >= 7 && var.kms_key_deletion_window_days <= 30
    error_message = "kms_key_deletion_window_days must be between 7 and 30."
  }
}
