# Writing New Terraform Architecture — From Zero to Deployed

A practical walkthrough: from "I have an idea for new infra" → "it's running in AWS, in code, version-controlled."

---

## 1. The language: HCL (HashiCorp Configuration Language)

Terraform doesn't use Python, JavaScript, or any general-purpose language. It uses **HCL** — a domain-specific language built specifically for declaring infrastructure.

### Why HCL exists

| Concern | Why HCL is designed this way |
|---|---|
| **Declarative, not imperative** | You describe *what* you want (a VPC with CIDR X), not *how* to create it (call API A, then B, then C). Terraform figures out the order. |
| **Human-readable** | JSON for infra is brutal. YAML is fragile (indentation). HCL is both readable and machine-parseable. |
| **Static** | No loops or recursion that depend on runtime state. The whole config can be analyzed by Terraform before running. That's how `terraform plan` works at all. |
| **Typed** | Strings, numbers, lists, maps, objects. Type errors caught at plan time, not at apply. |

### How HCL compares to alternatives

| Tool | Language | Pros | Cons |
|---|---|---|---|
| **Terraform** | HCL | Cloud-agnostic, huge ecosystem, declarative, mature | DSL = limited expressiveness; some logic feels awkward |
| **AWS CloudFormation** | JSON / YAML | Native to AWS, no provider drift | AWS-only, slow, verbose YAML |
| **AWS CDK** | TypeScript / Python / Go / Java | Real programming language | Generates CloudFormation under the hood; AWS-only |
| **Pulumi** | TypeScript / Python / Go / .NET | Real language + state model like Terraform | Smaller ecosystem, newer |
| **Ansible** | YAML + Jinja | Strong on config management | Imperative — bad for cloud provisioning |

**For backend engineering in 2026, HCL is the lingua franca.** Every Indian product company (Razorpay, CRED, Postman, Atlassian India, Stripe India) uses Terraform. Pulumi is gaining ground but Terraform is still 80 % of the market.

### HCL has exactly five top-level building blocks

```hcl
# 1. resource — creates and manages something in the cloud
resource "aws_s3_bucket" "uploads" {
  bucket = "fittbot-uploads"
}

# 2. data — reads something that already exists
data "aws_caller_identity" "current" {}

# 3. variable — input to your config (overridable)
variable "region" {
  type    = string
  default = "ap-south-2"
}

# 4. output — value exposed by your config
output "bucket_name" {
  value = aws_s3_bucket.uploads.id
}

# 5. module — call to reusable code
module "networking" {
  source = "../../modules/networking"
  vpc_cidr = "10.0.0.0/16"
}
```

Plus three "meta" blocks that configure Terraform itself:

```hcl
terraform { required_version = ">= 1.5.0" }   # which Terraform version
provider "aws" { region = var.region }         # which cloud, how to talk to it
locals { name_prefix = "fittbot-${var.env}" }  # computed values
```

That's the whole language. **8 block types.** Everything else is HCL expressions and types.

### HCL syntax in 60 seconds

```hcl
# ── comments use # or //

# ── strings
name = "hello"
multiline = <<-EOF
This is a
multi-line string.
EOF

# ── numbers
count = 3
ratio = 0.8

# ── booleans
enabled = true

# ── lists
azs = ["ap-south-2a", "ap-south-2b", "ap-south-2c"]

# ── maps
tags = {
  Project     = "fittbot"
  Environment = "prod"
}

# ── object (typed map)
config = {
  cpu    = 1024
  memory = 2048
  image  = "fittbot/backend:v1"
}

# ── string interpolation
bucket = "fittbot-${var.environment}-uploads"

# ── references
vpc_id = aws_vpc.main.id              # reference another resource
subnet = var.subnet_id                # reference a variable
region = data.aws_region.current.id   # reference a data source

# ── conditionals
desired_count = var.environment == "prod" ? 2 : 1

# ── for expressions (list comprehension)
public_subnet_ids = [for s in aws_subnet.public : s.id]

# ── functions (built-in)
name = lower(var.project)                              # → "fittbot"
tags = merge(var.common_tags, { Owner = "naveen" })    # combine maps
joined = join(",", var.azs)                            # → "ap-south-2a,ap-south-2b,ap-south-2c"
```

Full function reference: https://developer.hashicorp.com/terraform/language/functions

---

## 2. The thinking — from requirement to resources

Before you write a line of HCL, you need to know **what AWS services solve your problem**.

### The 5-step thinking framework

| Step | Question | Example (for a static website) |
|---|---|---|
| 1 | What does the user experience? | "User visits `app.fymble.app` and sees a React app instantly" |
| 2 | What's the entry point? | DNS → CDN → origin server |
| 3 | What stores the actual content? | S3 bucket holding HTML/JS/CSS |
| 4 | What controls access? | CloudFront distribution + Origin Access Control |
| 5 | What ties it together? | Route 53 record → CloudFront → S3 |

That translates to AWS resources:

| Need | AWS resource | Terraform resource |
|---|---|---|
| Store content | S3 bucket | `aws_s3_bucket` |
| Make it private to CDN only | OAC + bucket policy | `aws_cloudfront_origin_access_control` + `aws_s3_bucket_policy` |
| CDN | CloudFront distribution | `aws_cloudfront_distribution` |
| Custom domain | Route 53 record + ACM cert | `aws_route53_record` + `aws_acm_certificate` |
| HTTPS | Cert validated via DNS | `aws_acm_certificate_validation` |

**Skill:** Mapping a user requirement to a set of AWS resources. This is what differentiates juniors from seniors. You don't memorize HCL syntax — you memorize "user wants static site → S3 + CloudFront + ACM + Route 53."

### How to learn this

- AWS Architecture Center (https://aws.amazon.com/architecture/) — reference architectures for everything
- AWS Whitepapers — the "Well-Architected Framework" is canonical
- Read other people's Terraform — github.com search for `terraform aws static website`

---

## 3. The 7-step workflow for new infrastructure

```
1. THINK      Sketch the architecture on paper / Excalidraw / draw.io
              Identify AWS services needed.

2. SCAFFOLD   Create a folder. Add backend.tf, providers.tf, versions.tf,
              variables.tf, main.tf, outputs.tf.

3. WRITE      In main.tf, write resource blocks for each AWS service.
              Use variables for anything that might change.

4. INIT       terraform init
              Downloads providers + modules. Connects to backend.

5. PLAN       terraform plan
              Read-only. Shows what will be created.

6. APPLY      terraform apply
              Asks "yes/no". On yes, creates resources, updates state.

7. ITERATE    Edit HCL. terraform plan. terraform apply. Repeat.
              When done: git commit, git push.
```

---

## 4. The real walkthrough — building a static website (S3 + CloudFront)

You can run this **right now** without touching your prod. It creates new resources — nothing destructive.

### Step 4.1 — Think

Goal: serve a static HTML page at a custom domain over HTTPS.

Components:
- S3 bucket (stores HTML)
- CloudFront distribution (CDN in front of S3)
- Origin Access Control (so only CloudFront can read the bucket)
- (Optional) ACM cert + Route 53 record for custom domain — skip for now to keep simple

### Step 4.2 — Scaffold

```bash
cd "/Users/apple/Documents/Dev-admin/Fittbot Current AWS Version/infrastructure"
mkdir -p learning-sandbox/static-site
cd learning-sandbox/static-site
touch versions.tf providers.tf variables.tf main.tf outputs.tf
```

This `learning-sandbox/` folder is YOUR practice area. **Use local state, no backend** (we'll skip the S3 state backend setup for the sandbox).

### Step 4.3 — Write the code

```hcl
# ── versions.tf ────────────────────────────────────────────────────
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
  }
}
```

```hcl
# ── providers.tf ───────────────────────────────────────────────────
provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "fittbot-learning"
      ManagedBy = "terraform"
      Purpose   = "learning-sandbox"
    }
  }
}
```

```hcl
# ── variables.tf ───────────────────────────────────────────────────
variable "region" {
  type        = string
  default     = "ap-south-2"
  description = "AWS region for resources (CloudFront itself is global)"
}

variable "project_name" {
  type        = string
  default     = "fittbot-learning-site"
  description = "Used to namespace all resources"
}
```

Now the main resources:

```hcl
# ── main.tf ────────────────────────────────────────────────────────

# A random suffix so the bucket name is globally unique
resource "random_id" "bucket_suffix" {
  byte_length = 4
}

# ── The S3 bucket that holds the website files ─────────────────────
resource "aws_s3_bucket" "site" {
  bucket = "${var.project_name}-${random_id.bucket_suffix.hex}"
}

# Block all public access — only CloudFront can read this bucket
resource "aws_s3_bucket_public_access_block" "site" {
  bucket = aws_s3_bucket.site.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Versioning — enables rollback of files
resource "aws_s3_bucket_versioning" "site" {
  bucket = aws_s3_bucket.site.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Upload a basic index.html so we have something to view
resource "aws_s3_object" "index" {
  bucket       = aws_s3_bucket.site.id
  key          = "index.html"
  content      = <<-HTML
    <!DOCTYPE html>
    <html>
      <head><title>Fittbot Learning</title></head>
      <body style="font-family: system-ui; padding: 40px; text-align: center;">
        <h1>🚀 Hello from Terraform!</h1>
        <p>This page is served by S3 + CloudFront, both provisioned by Terraform.</p>
      </body>
    </html>
  HTML
  content_type = "text/html"
  etag         = md5("<!DOCTYPE html>...")  # forces re-upload on content change
}

# ── CloudFront Origin Access Control (modern replacement for OAI) ──
resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "${var.project_name}-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# ── The CloudFront distribution itself ─────────────────────────────
resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"
  comment             = "Static site distribution for ${var.project_name}"
  price_class         = "PriceClass_100"  # cheapest — US/Canada/EU only edge locations

  origin {
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_id                = "s3-origin"
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  default_cache_behavior {
    target_origin_id       = "s3-origin"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]

    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }

    min_ttl     = 0
    default_ttl = 3600
    max_ttl     = 86400
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

# ── Bucket policy that allows ONLY this CloudFront distribution to read ──
data "aws_iam_policy_document" "site_bucket" {
  statement {
    sid     = "AllowCloudFrontServicePrincipal"
    actions = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.site.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.site.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = data.aws_iam_policy_document.site_bucket.json
}
```

```hcl
# ── outputs.tf ─────────────────────────────────────────────────────
output "bucket_name" {
  value       = aws_s3_bucket.site.id
  description = "The S3 bucket where website content lives"
}

output "cloudfront_url" {
  value       = "https://${aws_cloudfront_distribution.site.domain_name}"
  description = "Visit this URL in your browser"
}

output "cloudfront_distribution_id" {
  value       = aws_cloudfront_distribution.site.id
  description = "Use this for invalidations: aws cloudfront create-invalidation --distribution-id ..."
}
```

### Step 4.4 — Init

```bash
terraform init
```

Output:
```
Initializing the backend...
Initializing provider plugins...
- Finding hashicorp/aws versions matching "~> 5.0"...
- Installing hashicorp/aws v5.83.0...
Terraform has been successfully initialized!
```

A `.terraform/` directory is created with the AWS provider. A `.terraform.lock.hcl` file is created — pins the exact provider version.

### Step 4.5 — Plan

```bash
terraform plan
```

Sample output:
```
Terraform will perform the following actions:

  # aws_cloudfront_distribution.site will be created
  + resource "aws_cloudfront_distribution" "site" {
      + arn               = (known after apply)
      + domain_name       = (known after apply)
      + enabled           = true
      ...
    }

  # aws_cloudfront_origin_access_control.site will be created
  + resource "aws_cloudfront_origin_access_control" "site" {
      + id                                = (known after apply)
      + name                              = "fittbot-learning-site-oac"
      ...
    }

  # aws_s3_bucket.site will be created
  + resource "aws_s3_bucket" "site" {
      + bucket = "fittbot-learning-site-a1b2c3d4"
      ...
    }

  # ... more resources ...

Plan: 7 to add, 0 to change, 0 to destroy.

Changes to Outputs:
  + bucket_name              = (known after apply)
  + cloudfront_distribution_id = (known after apply)
  + cloudfront_url           = (known after apply)
```

Read every line. Confirm:
- 7 "to add" matches what you expect (1 bucket + 4 bucket configs + 1 OAC + 1 distribution + 1 policy + 1 object — close enough)
- 0 "to change" / 0 "to destroy"

### Step 4.6 — Apply

```bash
terraform apply
# Plan: 7 to add, 0 to change, 0 to destroy.
# Do you want to perform these actions? Type yes
```

Type `yes`. Terraform now:
1. Creates the S3 bucket (~2s)
2. Sets bucket policies (parallel — ~3s)
3. Uploads index.html (~1s)
4. Creates the OAC (~5s)
5. Creates the CloudFront distribution (~5–15 min — CloudFront is SLOW to provision)

Final output:
```
Apply complete! Resources: 7 added, 0 changed, 0 destroyed.

Outputs:
bucket_name = "fittbot-learning-site-a1b2c3d4"
cloudfront_url = "https://d1234abcd.cloudfront.net"
cloudfront_distribution_id = "EXXXXXXXXXX"
```

Open the `cloudfront_url` in a browser. You should see "🚀 Hello from Terraform!"

### Step 4.7 — Iterate

Want to change the page content?

Edit `main.tf`:
```hcl
content = <<-HTML
  <!DOCTYPE html>
  <html>
    <body>
      <h1>Updated page!</h1>
    </body>
  </html>
HTML
```

Run:
```bash
terraform plan
# ~ aws_s3_object.index will be updated in-place
# Plan: 0 to add, 1 to change, 0 to destroy.

terraform apply
# Apply complete! Resources: 0 added, 1 changed, 0 destroyed.

# Invalidate CloudFront cache so the new file shows immediately
aws cloudfront create-invalidation \
  --distribution-id $(terraform output -raw cloudfront_distribution_id) \
  --paths "/*"
```

Refresh the browser — new content.

### Step 4.8 — Tear it down when done

```bash
terraform destroy
# Plan: 0 to add, 0 to change, 7 to destroy.
# Type yes
```

Everything in this sandbox is gone. **Zero impact on your prod** because none of the resources are shared.

---

## 5. The patterns you'll use repeatedly

### Pattern 1: A resource references another's attribute

```hcl
resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
}

resource "aws_subnet" "web" {
  vpc_id     = aws_vpc.main.id   # implicit dependency on aws_vpc.main
  cidr_block = "10.0.1.0/24"
}
```

Terraform builds the dependency graph from these references and creates the VPC before the subnet automatically.

### Pattern 2: Loop with `for_each`

```hcl
variable "buckets" {
  type    = set(string)
  default = ["uploads", "logs", "exports"]
}

resource "aws_s3_bucket" "buckets" {
  for_each = var.buckets
  bucket   = "fittbot-${each.key}"
}

# Reference one: aws_s3_bucket.buckets["uploads"].id
```

### Pattern 3: Compose modules

```hcl
module "vpc" {
  source = "terraform-aws-modules/vpc/aws"
  version = "5.0.0"

  name = "fittbot-prod"
  cidr = "10.0.0.0/16"
  azs             = ["ap-south-2a", "ap-south-2b", "ap-south-2c"]
  public_subnets  = ["10.0.0.0/24", "10.0.1.0/24", "10.0.2.0/24"]
  private_subnets = ["10.0.10.0/24", "10.0.11.0/24", "10.0.12.0/24"]
}

# Reference: module.vpc.vpc_id, module.vpc.private_subnet_ids
```

Public modules from the Terraform Registry save you hundreds of lines of HCL: https://registry.terraform.io/

### Pattern 4: Conditional creation

```hcl
resource "aws_db_instance" "read_replica" {
  count = var.environment == "prod" ? 1 : 0

  identifier            = "${var.project}-read-replica"
  instance_class        = "db.t4g.small"
  replicate_source_db   = aws_db_instance.primary.identifier
}
```

When `count = 0`, the resource isn't created. When `count = 1`, one is created.

### Pattern 5: Data source for cross-stack reference

```hcl
data "aws_vpc" "existing" {
  tags = { Name = "fittbot-prod-vpc" }
}

resource "aws_security_group" "new_sg" {
  vpc_id = data.aws_vpc.existing.id   # uses the existing VPC
}
```

### Pattern 6: Sensitive variables

```hcl
variable "db_password" {
  type      = string
  sensitive = true   # won't show in plan output or terraform output
}
```

Pass it via:
```bash
export TF_VAR_db_password='...'   # via env
terraform plan
```
Or via a `.auto.tfvars` file that's in `.gitignore`. **Never commit secrets.**

---

## 6. The 5-step mental checklist for every new architecture

Before you run `terraform apply` on anything new:

1. **Have I drawn it?** A 1-minute sketch on paper saves an hour of "wait, what did I want?"
2. **Have I listed every AWS resource I need?** If you can't list them, you don't know the architecture yet.
3. **Have I checked the Terraform Registry?** 90 % of common patterns have a maintained module already.
4. **Have I read `terraform plan` line by line?** Every `-/+` is a red flag. Every "forces replacement" is a question.
5. **Does this need `lifecycle { prevent_destroy = true }`?** If losing this resource would lose data, yes.

If you can answer all 5, you're safe to apply.

---

## 7. Where to learn more (in order of usefulness)

| Resource | Why |
|---|---|
| **terraform.io/docs** — official docs | Searchable, accurate, free |
| **registry.terraform.io** | Browse 4,000+ public modules. Don't reinvent wheels. |
| **github.com search: `terraform aws <thing>`** | See how real codebases solve the same problem |
| **HashiCorp Learn** — learn.hashicorp.com | Hands-on tutorials, certification path |
| **Gruntwork blog** | Deep, real-world Terraform from a consulting firm |
| **Terraform Up & Running** (book by Yevgeniy Brikman) | Best Terraform book, period. Worth the ₹2,500. |

---

## 8. Try it now — run the static-site example

```bash
mkdir -p "infrastructure/learning-sandbox/static-site"
cd "infrastructure/learning-sandbox/static-site"

# Copy the HCL from section 4.3 into these files:
#   versions.tf, providers.tf, variables.tf, main.tf, outputs.tf

terraform init
terraform plan      # read carefully
terraform apply     # type yes when ready (CloudFront creation takes ~10 min)
```

After the apply, visit the `cloudfront_url` output in a browser. Then change the HTML, plan, apply, invalidate, refresh — you've just done a complete IaC deploy loop.

When you're done playing:
```bash
terraform destroy
```

Done. You've gone from zero to deployed in one sitting.

---

## 9. The one-paragraph mental model

> **HCL is a declarative DSL for AWS infrastructure. You write `resource` blocks describing what you want. Terraform parses them into a dependency graph, refreshes state from AWS, computes a plan (the diff), and on `apply` executes the plan via AWS APIs — creating, updating, or destroying resources in the right order, in parallel where possible. The state file records what Terraform owns. Everything else is patterns layered on top.**

Internalize that, and writing new Terraform becomes mechanical. The skill that's hard isn't HCL — it's knowing **which AWS resources you need** for a given problem. That comes from reading other people's architectures.
