"""
Centralized S3 service for presigned URL generation and upload operations.

Eliminates hardcoded AWS_REGION / BUCKET_NAME / boto3.client scattered
across 27+ endpoint files.
"""

import time
import logging
from typing import Optional

import boto3
from botocore.config import Config

from app.config.settings import settings
from app.utils.logging_utils import FittbotHTTPException

_log = logging.getLogger("app.services.s3_service")

# ── Constants ───────────────────────────────────────────────

AWS_REGION = "ap-south-2"
BUCKET_NAME = "fittbot-uploads"
DEFAULT_PRESIGN_EXPIRES = 600  # 10 minutes


# ── Shared S3 clients (module-level singletons) ────────────

_upload_s3 = boto3.client("s3", region_name=AWS_REGION)

_pdf_s3 = boto3.client(
    "s3",
    region_name=settings.aws_region,
    config=Config(retries={"max_attempts": 5, "mode": "standard"}),
)


def get_upload_s3():
    """Return the shared S3 client used for user/gym uploads."""
    return _upload_s3


def get_pdf_s3():
    """Return the shared S3 client used for PDF/document operations."""
    return _pdf_s3


# ── Presigned POST (browser direct upload) ──────────────────

def generate_presigned_post(
    key: str,
    content_type: str,
    max_size: int,
    bucket: str = BUCKET_NAME,
    expires: int = DEFAULT_PRESIGN_EXPIRES,
) -> dict:
    """
    Generate an S3 presigned POST with content-type and size constraints.

    Returns the presigned form dict (fields + url).
    """
    if not content_type:
        raise FittbotHTTPException(
            status_code=400,
            detail="Content type is required",
            error_code="MISSING_CONTENT_TYPE",
            log_data={"key": key},
        )

    fields = {"Content-Type": content_type}
    conditions = [
        {"Content-Type": content_type},
        ["content-length-range", 1, max_size],
    ]

    try:
        presigned = _upload_s3.generate_presigned_post(
            Bucket=bucket,
            Key=key,
            Fields=fields,
            Conditions=conditions,
            ExpiresIn=expires,
        )
    except Exception as e:
        raise FittbotHTTPException(
            status_code=500,
            detail="Failed to generate S3 presigned POST",
            error_code="S3_PRESIGNED_POST_ERROR",
            log_data={"error": repr(e), "key": key},
        ) from e

    # Rewrite URL to the canonical S3 endpoint for CDN compatibility
    presigned["url"] = f"https://{bucket}.s3.{AWS_REGION}.amazonaws.com/"
    return presigned


def build_cdn_url(presigned_url: str, key: str) -> str:
    """Build a cache-busted CDN URL from a presigned base URL and key."""
    version = int(time.time() * 1000)
    return f"{presigned_url}{key}?v={version}"


# ── Presigned GET (download) ────────────────────────────────

def generate_presigned_get(
    key: str,
    bucket: str = None,
    expires: int = None,
) -> str:
    """Generate a presigned GET URL for downloading from S3."""
    bucket = bucket or settings.pdf_s3_bucket
    expires = expires or settings.pdf_presign_expires_seconds
    return _pdf_s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires,
    )
