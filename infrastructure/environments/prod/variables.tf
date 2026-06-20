variable "region" {
  type        = string
  default     = "ap-south-2"
  description = "AWS region for all prod resources"
}

variable "account_id" {
  type        = string
  default     = "182399696098"
  description = "AWS account ID — used for ARN construction"
}

variable "project" {
  type        = string
  default     = "fittbot"
  description = "Project name, used as a prefix for resource names"
}

variable "environment" {
  type        = string
  default     = "prod"
  description = "Environment name — drives tagging and naming"
}
