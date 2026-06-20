# ─────────────────────────────────────────────────────────────────────────
# Import blocks — Networking
#
# Phase 3.2 of the runbook. Imports VPC + subnets + NAT + IGW + route
# tables + S3 VPC endpoint into Terraform state.
#
# These IDs come from `aws ec2 describe-*` runs (see AWS_INVENTORY_LIVE.md).
# After import, delete this file — the resources are now owned by the
# networking module.
# ─────────────────────────────────────────────────────────────────────────

# Note: Until module.networking is uncommented in main.tf, these blocks
# will fail with "no resource address found". Uncomment in main.tf first.

# import {
#   to = module.networking.aws_vpc.main
#   id = "vpc-0f268fb3dc0dd0600"
# }
#
# import {
#   to = module.networking.aws_internet_gateway.main
#   id = "igw-0709a4e3db58ecc48"
# }
#
# import {
#   to = module.networking.aws_subnet.public[0]
#   id = "subnet-06fd7a321c77c1def"      # ap-south-2a, public, NAT lives here
# }
# import {
#   to = module.networking.aws_subnet.public[1]
#   id = "subnet-00c1b09f6f02aff10"      # ap-south-2b, public
# }
# import {
#   to = module.networking.aws_subnet.public[2]
#   id = "subnet-04e1a6607004b72a9"      # ap-south-2c, public
# }
# import {
#   to = module.networking.aws_subnet.private[0]
#   id = "subnet-0c2101a6a7e7bf803"      # ap-south-2a, private, RDS primary
# }
# import {
#   to = module.networking.aws_subnet.private[1]
#   id = "subnet-0b59975c322d501c6"      # ap-south-2b, private
# }
# import {
#   to = module.networking.aws_subnet.private[2]
#   id = "subnet-0d9f1514dcdf67874"      # ap-south-2c, private, Redis lives here
# }
#
# import {
#   to = module.networking.aws_eip.nat
#   id = "eipassoc-01d09cdc8758ca6b9"    # association ID, see Note below
# }
# # Note: AWS EIPs are imported by allocation ID, not association ID.
# # Run: aws ec2 describe-addresses --public-ips 18.60.96.58
# # to get the allocation-id (eipalloc-...) and use that as the import id.
#
# import {
#   to = module.networking.aws_nat_gateway.main
#   id = "nat-04867289b85560c1e"
# }
#
# # Route tables — find their actual IDs first:
# # aws ec2 describe-route-tables --filters Name=vpc-id,Values=vpc-0f268fb3dc0dd0600
# # The output of that gave us 3 route tables:
# #   rtb-097b2661b7a94138a  (isolated, 1 subnet)
# #   rtb-0188f79ed640b2bf3  (private, 3 subnets, → NAT + VPCE)
# #   rtb-03eb98b1bdf0ccee2  (public, 3 subnets, → IGW)
# # The "isolated" RT (no internet route) isn't represented in this module — it's
# # leftover. Either import it as a 3rd RT or leave it unmanaged.
#
# import {
#   to = module.networking.aws_route_table.public
#   id = "rtb-03eb98b1bdf0ccee2"
# }
# import {
#   to = module.networking.aws_route_table.private
#   id = "rtb-0188f79ed640b2bf3"
# }
#
# # Route table associations — composite IDs: <subnet-id>/<rtb-id>
# # Get exact IDs from: aws ec2 describe-route-tables --route-table-id <id>
# #
# # import {
# #   to = module.networking.aws_route_table_association.public[0]
# #   id = "subnet-06fd7a321c77c1def/rtb-03eb98b1bdf0ccee2"
# # }
# # ... and so on for public[1..2], private[0..2]
#
# import {
#   to = module.networking.aws_vpc_endpoint.s3
#   id = "vpce-0d53cf8d2a4c3829e"
# }
