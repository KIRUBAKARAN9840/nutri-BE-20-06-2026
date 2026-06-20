variable "cluster_name" {
  description = "Name of the EKS cluster"
  type        = string
}

variable "subnet_ids" {
  description = "Subnet IDs where bootstrap nodes will run"
  type        = list(string)
}

variable "instance_types" {
  description = "EC2 instance types for bootstrap nodes"
  type        = list(string)
  default     = ["t3.medium"]
}

variable "desired_size" {
  description = "Desired number of bootstrap nodes"
  type        = number
  default     = 1
}

variable "min_size" {
  description = "Minimum number of bootstrap nodes"
  type        = number
  default     = 1
}

variable "max_size" {
  description = "Maximum number of bootstrap nodes"
  type        = number
  default     = 2
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
