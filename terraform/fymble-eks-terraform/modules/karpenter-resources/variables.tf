variable "cluster_name" {
  description = "Name of the EKS cluster"
  type        = string
}

variable "karpenter_version" {
  description = "Version of the Karpenter Helm chart"
  type        = string
  default     = "1.0.6"
}

variable "controller_iam_role_arn" {
  description = "IAM role ARN for the Karpenter controller (IRSA)"
  type        = string
}

variable "node_iam_role_name" {
  description = "IAM role name that Karpenter-created nodes will use"
  type        = string
}

variable "interruption_queue_name" {
  description = "SQS queue name for spot interruption events"
  type        = string
}

variable "instance_types" {
  description = "Allowed EC2 instance types for Karpenter-provisioned nodes"
  type        = list(string)
  default     = ["t3.medium", "t3.large", "t3.xlarge", "m5.large", "m5.xlarge"]
}

variable "capacity_types" {
  description = "Allowed capacity types (on-demand / spot)"
  type        = list(string)
  default     = ["on-demand"]
}

variable "cpu_limit" {
  description = "Maximum total CPU Karpenter can provision (cost safety cap)"
  type        = number
  default     = 100
}

variable "memory_limit_gi" {
  description = "Maximum total memory in GiB (cost safety cap)"
  type        = number
  default     = 100
}
