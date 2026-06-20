# References to AWS resources that already exist outside this Terraform config.

data "aws_vpc" "fymble" {
  id = var.vpc_id
}

data "aws_subnet" "eks_private" {
  for_each = toset(var.eks_subnet_ids)
  id       = each.value
}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

data "aws_region" "current" {}
