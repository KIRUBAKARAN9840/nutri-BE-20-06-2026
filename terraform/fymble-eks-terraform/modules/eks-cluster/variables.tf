variable "cluster_name" {
  description = "Name of the EKS cluster."
  type        = string
}

variable "kubernetes_version" {
  description = "Kubernetes version for the EKS control plane."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID hosting the cluster."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets (>=2 AZs) for the control plane ENIs."
  type        = list(string)
}

variable "endpoint_public_access" {
  description = "Enable the public API endpoint."
  type        = bool
  default     = true
}

variable "endpoint_public_access_cidrs" {
  description = "Allowed CIDRs for the public API endpoint."
  type        = list(string)
  default     = []
}

variable "log_types" {
  description = "Control-plane log types enabled."
  type        = list(string)
  default     = ["api", "audit", "authenticator", "controllerManager", "scheduler"]
}

variable "cloudwatch_log_retention_days" {
  description = "Retention period for the cluster's CloudWatch log group."
  type        = number
  default     = 90
}

variable "kms_key_deletion_window_days" {
  description = "Pending-deletion window for the KMS key."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Tags applied to every resource this module creates."
  type        = map(string)
  default     = {}
}
