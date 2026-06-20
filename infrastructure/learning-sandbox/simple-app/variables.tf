variable "region" {
  type    = string
  default = "ap-south-2"
}

variable "project" {
  type    = string
  default = "fittbot"
}

variable "environment" {
  type    = string
  default = "staging"
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16" # 10.20 = staging (10.0 = prod, 10.10 = dev, 10.20 = staging convention)
}

variable "azs" {
  type    = list(string)
  default = ["ap-south-2a", "ap-south-2b", "ap-south-2c"]
}

variable "public_subnet_cidrs" {
  type    = list(string)
  default = ["10.20.0.0/24", "10.20.1.0/24", "10.20.2.0/24"]
}

variable "private_subnet_cidrs" {
  type    = list(string)
  default = ["10.20.10.0/24", "10.20.11.0/24", "10.20.12.0/24"]
}

variable "rds_password" {
  type      = string
  sensitive = true
  # Pass via: export TF_VAR_rds_password='...'  (NEVER commit this)
}
