"""
Generate AWS architecture diagrams using the `diagrams` library.

Run:
    source ../venv/bin/activate
    python generate_aws_diagram.py

Outputs (PNG, with official AWS service icons):
    aws_high_level.png
    aws_detailed.png
    aws_data_flow.png
"""
from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import ECS, Fargate, Lambda, EC2
from diagrams.aws.database import RDS, ElastiCache
from diagrams.aws.network import (
    ALB, VPC, NATGateway, InternetGateway, Route53, CloudFront, PrivateSubnet, PublicSubnet, VPCRouter
)
from diagrams.aws.security import WAF, KMS, SecretsManager, ACM
from diagrams.aws.storage import S3
from diagrams.aws.integration import SQS, Eventbridge
from diagrams.aws.management import Cloudwatch, CloudwatchLogs
from diagrams.aws.devtools import Codepipeline, Codedeploy
from diagrams.aws.compute import ECR
from diagrams.onprem.client import Users, Client
from diagrams.onprem.network import Internet
from diagrams.saas.cdn import Cloudflare
from diagrams.saas.chat import Slack
from diagrams.onprem.queue import Celery
from diagrams.programming.framework import Fastapi

graph_attr = {
    "fontsize": "16",
    "fontname": "Helvetica",
    "bgcolor": "white",
    "pad": "0.6",
    "nodesep": "0.5",
    "ranksep": "0.7",
}

node_attr = {"fontsize": "12", "fontname": "Helvetica"}

# ────────────────────────────────────────────────────────────────────────────
# Diagram 1: HIGH-LEVEL EXECUTIVE VIEW
# ────────────────────────────────────────────────────────────────────────────
with Diagram(
    "Fymble · Fittbot — AWS Architecture (Production · ap-south-2)",
    show=False,
    filename="aws_high_level",
    direction="TB",
    graph_attr=graph_attr,
    node_attr=node_attr,
):
    users = Users("Users\n(Mobile + Web)")
    dns = Route53("Route 53\nDNS")

    with Cluster("AWS Account 182399696098 · ap-south-2 (Hyderabad)"):
        waf = WAF("WAF")
        alb = ALB("ALB\ndev-lb-new")

        with Cluster("ECS Fargate Cluster\ndev-codedeploy-cluster-test"):
            api = Fargate("Fittbot-Production\n×2 · 1 vCPU / 2 GB")
            with Cluster("Celery Workers"):
                w_pay = Fargate("Payments\n1 vCPU / 3 GB")
                w_gen = Fargate("AI + General\n1 vCPU / 3 GB")
                w_diet = Fargate("AI-Diet (gevent)\n0.5 / 1 GB")
                w_act = Fargate("Client-Tracking\n0.5 / 1 GB")
            w_rem = Fargate("Reminder Poller\n0.25 / 1 GB")
            pg = Fargate("Payment Gateway UI\n0.5 / 1 GB")

        with Cluster("Data Tier"):
            rds = RDS("RDS MySQL\ndevfittbotdb\ndb.t3.medium")
            cache = ElastiCache("ElastiCache Redis\ncache.r7g.large")
            uploads = S3("S3\nfittbot-uploads")

        with Cluster("Serverless"):
            lambdas = Lambda("Lambda ×8\n(reminders, attendance)")
            eb = Eventbridge("EventBridge\n4 schedules")
            sqs = SQS("SQS\nreminder queue")

        with Cluster("Security & Secrets"):
            secrets = SecretsManager("Secrets Mgr\n7 secrets")
            kms = KMS("KMS\n4 keys")

        cwl = CloudwatchLogs("CloudWatch\n110 log groups")
        ecr = ECR("ECR\n2 repos")

    with Cluster("External APIs"):
        razorpay = Client("Razorpay +\nRazorpayX")
        ai_apis = Client("OpenAI · Groq ·\nGemini")
        push = Client("Firebase FCM ·\nExpo Push")
        comm = Client("WhatsApp ·\nBhashSMS · SMTP")

    # Wiring
    users >> Edge(label="HTTPS") >> dns >> waf >> alb
    alb >> Edge(label="port 8000") >> api
    alb >> Edge(label="port 3000") >> pg

    api >> Edge(color="blue", label="cache+pubsub") >> cache
    api >> Edge(color="darkgreen") >> rds
    api >> Edge(label="uploads") >> uploads
    api >> Edge(style="dashed", label="enqueue") >> [w_pay, w_gen, w_diet, w_act]

    [w_pay, w_gen, w_diet, w_act] >> Edge(color="darkgreen") >> rds
    [w_pay, w_gen, w_diet, w_act] >> Edge(color="blue") >> cache

    w_pay >> Edge(color="red", label="charges") >> razorpay
    [w_gen, w_diet] >> Edge(color="purple") >> ai_apis
    [w_act, api] >> Edge(color="orange") >> comm
    api >> Edge(color="orange") >> push

    eb >> lambdas >> sqs >> w_rem >> rds
    api >> secrets
    rds >> Edge(style="dotted") >> kms

# ────────────────────────────────────────────────────────────────────────────
# Diagram 2: DETAILED NETWORK / VPC LAYOUT
# ────────────────────────────────────────────────────────────────────────────
with Diagram(
    "Fymble — VPC Network Topology · vpc-0f268fb3dc0dd0600",
    show=False,
    filename="aws_network",
    direction="TB",
    graph_attr={**graph_attr, "ranksep": "1.0"},
    node_attr=node_attr,
):
    internet = Internet("Internet")

    with Cluster("VPC 10.0.0.0/16 · ap-south-2"):
        igw = InternetGateway("IGW\nigw-0709a4e3db58ecc48")

        with Cluster("Availability Zone: ap-south-2a"):
            with Cluster("Public Subnet 10.0.0.0/24"):
                nat = NATGateway("NAT GW\nEIP 18.60.96.58")
                alb_a = ALB("ALB Node A")
            with Cluster("Private Subnet 10.0.10.0/24"):
                rds_p = RDS("RDS Primary\ndevfittbotdb")
                ecs_a = ECS("Fargate Tasks")

        with Cluster("Availability Zone: ap-south-2b"):
            with Cluster("Public Subnet 10.0.1.0/24"):
                alb_b = ALB("ALB Node B")
            with Cluster("Private Subnet 10.0.11.0/24"):
                ecs_b = ECS("Fargate Tasks")

        with Cluster("Availability Zone: ap-south-2c"):
            with Cluster("Public Subnet 10.0.2.0/24"):
                alb_c = ALB("ALB Node C")
            with Cluster("Private Subnet 10.0.12.0/24"):
                redis = ElastiCache("Redis\ncache.r7g.large")
                ecs_c = ECS("Fargate Tasks")

        with Cluster("VPC Endpoints"):
            vpce_s3 = S3("S3 Gateway VPCE")

    internet >> Edge(label="443") >> igw
    igw >> [alb_a, alb_b, alb_c]
    igw >> nat

    [ecs_a, ecs_b, ecs_c] >> Edge(label="outbound\nHTTPS") >> nat
    [ecs_a, ecs_b, ecs_c] >> vpce_s3

    [ecs_a, ecs_b, ecs_c] >> Edge(color="darkgreen", label="3306") >> rds_p
    [ecs_a, ecs_b, ecs_c] >> Edge(color="blue", label="6379") >> redis

# ────────────────────────────────────────────────────────────────────────────
# Diagram 3: DATA & REQUEST FLOW
# ────────────────────────────────────────────────────────────────────────────
with Diagram(
    "Fymble — Request & Payment Data Flow",
    show=False,
    filename="aws_data_flow",
    direction="LR",
    graph_attr=graph_attr,
    node_attr=node_attr,
):
    user = Client("Mobile App")

    with Cluster("Edge"):
        r53 = Route53("Route 53")
        waf = WAF("WAF")
        alb = ALB("ALB")

    with Cluster("FastAPI API (Fargate ×2)"):
        api = Fastapi("FastAPI\nUvicorn")

    with Cluster("Hot Path (Redis)"):
        redis = ElastiCache("Redis\n(cache + broker + pubsub)")

    with Cluster("Async Workers"):
        celery_pay = Celery("Payments Worker\n(prefork)")
        celery_gen = Celery("General Worker\n(prefork)")
        celery_diet = Celery("AI Diet Worker\n(gevent)")

    with Cluster("Storage"):
        db = RDS("RDS MySQL\n(orders · clients · ...)")
        s3 = S3("S3 fittbot-uploads")

    with Cluster("External"):
        rzp = Client("Razorpay")
        oai = Client("OpenAI")
        fcm = Client("Firebase\nFCM")
        wa = Client("WhatsApp")

    user >> Edge(label="HTTPS") >> r53 >> waf >> alb >> api
    api >> Edge(label="GET cache", color="blue") >> redis
    api >> Edge(label="cache miss", color="darkgreen") >> db
    api >> Edge(label="upload", color="orange") >> s3

    api >> Edge(label="LPUSH payments", color="red") >> redis
    redis >> Edge(label="RPOP", color="red") >> celery_pay
    celery_pay >> Edge(label="charge", color="red") >> rzp
    celery_pay >> Edge(label="UPDATE", color="darkgreen") >> db
    celery_pay >> Edge(label="PUBLISH", color="blue") >> redis
    redis >> Edge(label="SUBSCRIBE\nuser_channel", color="blue") >> api
    api >> Edge(label="WebSocket push") >> user

    api >> Edge(label="LPUSH ai") >> redis
    redis >> celery_gen >> oai
    redis >> celery_diet >> oai

    celery_gen >> Edge(label="push") >> fcm
    api >> Edge(label="send OTP") >> wa


print("Generated:")
print("  - aws_high_level.png")
print("  - aws_network.png")
print("  - aws_data_flow.png")
