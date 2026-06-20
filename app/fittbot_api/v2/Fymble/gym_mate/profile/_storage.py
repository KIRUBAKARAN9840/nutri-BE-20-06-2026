"""S3 storage adapter for profile photos.

Wraps app.services.s3_service.generate_presigned_post() with module-specific
concerns:
  - Per-user S3 key prefix (security: each user owns gym_mate/profile/{id}/*)
  - Allowed content types (jpeg/png/webp)
  - Max upload size (5 MB)
  - Standard presign expiry (10 min)

The presigned POST flow:
  1. Client app calls POST /gym_mate/profile/photos/presign with N slots
  2. Backend returns N {url, fields, key} envelopes
  3. Client app uploads each photo directly to S3 via multipart/form-data POST
  4. Client app submits the resulting keys to /gym_mate/profile/onboarding/step2
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import List, Optional

from app.services import s3_service
from app.utils.logging_utils import FittbotHTTPException


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------
ALLOWED_CONTENT_TYPES = frozenset({
    "image/jpeg",
    "image/png",
    "image/webp",
})

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB safety rail — FE compresses before upload
PRESIGN_EXPIRES_SECONDS = 600       # 10 minutes — matches existing convention

_CONTENT_TYPE_EXTENSION = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


# ---------------------------------------------------------------------------
# CDN URL helper — used by presign AND by read endpoints (/me, summary)
# ---------------------------------------------------------------------------
def build_cdn_url(key: str, version_ms: Optional[int] = None) -> str:
    """Build a public URL the frontend can use to render the image.

    Today this points directly at the regional S3 endpoint. If you later
    add CloudFront / a CDN alias, change this function — every caller
    benefits automatically.

    `version_ms` adds a `?v=` cache-buster (matches the existing pattern
    in profile_pic.py / gym_profile.py / agreement_acceptance.py). Pass
    None on reads where the key already contains a UUID and a cache
    buster would just force needless re-downloads.
    """
    # Pass through full http(s) URLs (e.g. dummy/test DPs hosted on
    # picsum/cdns). Only real S3 keys get the bucket prefix.
    if key.startswith(("http://", "https://")):
        return key
    base = f"https://{s3_service.BUCKET_NAME}.s3.{s3_service.AWS_REGION}.amazonaws.com"
    if version_ms is not None:
        return f"{base}/{key}?v={version_ms}"
    return f"{base}/{key}"


# ---------------------------------------------------------------------------
# Public DTO (returned by the storage layer; surfaced via API + HTTP)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PresignedPhotoUpload:
    """One presigned upload slot the client can POST to.

    Mirrors the existing shape used by profile_pic / gym_profile /
    agreement_acceptance endpoints: a nested `upload` envelope (url +
    fields + key) plus a top-level `cdn_url` (cache-busted) and `version`.

    - `url, fields, key` : the S3 POST envelope
    - `cdn_url`          : the URL to use to render the image once uploaded
    - `version`          : cache-buster (epoch ms) embedded in cdn_url
    - `expires_in_seconds`: how long the presigned POST is valid
    """
    url: str
    fields: dict
    key: str
    cdn_url: str
    version: int
    expires_in_seconds: int


# ---------------------------------------------------------------------------
# Storage adapter
# ---------------------------------------------------------------------------
class ProfilePhotoStorage:
    """Generates presigned URLs scoped to a single user's S3 prefix."""

    KEY_PREFIX = "gym_mate/profile"

    def presign_uploads(
        self,
        client_id: int,
        slots: List["PresignSlotRequest"],
    ) -> List[PresignedPhotoUpload]:
        """Issue presigned POSTs for a list of (slot, content_type) pairs.

        Each key is `gym_mate/profile/{client_id}/{slot}_{uuid}.{ext}` —
        scoped to this user. Other users cannot generate a key under this
        prefix (the route uses get_verified_client_id), and step 2 validates
        the prefix again on submission as defense in depth.
        """
        if not slots:
            raise FittbotHTTPException(
                status_code=400,
                detail="At least one photo slot is required",
                error_code="GYMMATE_PRESIGN_NO_SLOTS",
                log_data={"client_id": client_id},
            )
        if len(slots) > 3:
            raise FittbotHTTPException(
                status_code=400,
                detail="At most 3 photos per onboarding",
                error_code="GYMMATE_PRESIGN_TOO_MANY",
                log_data={"client_id": client_id, "count": len(slots)},
            )

        results: List[PresignedPhotoUpload] = []
        for slot in slots:
            self._validate_content_type(slot.content_type, client_id)
            self._validate_display_order(slot.display_order, client_id)

            ext = _CONTENT_TYPE_EXTENSION[slot.content_type]
            key = (
                f"{self.KEY_PREFIX}/{client_id}/"
                f"{slot.display_order}_{uuid.uuid4().hex}.{ext}"
            )

            try:
                presigned = s3_service.generate_presigned_post(
                    key=key,
                    content_type=slot.content_type,
                    max_size=MAX_UPLOAD_BYTES,
                    expires=PRESIGN_EXPIRES_SECONDS,
                )
            except Exception as exc:    # pragma: no cover — surfaces S3 outage
                raise FittbotHTTPException(
                    status_code=503,
                    detail="Could not generate upload URL — try again",
                    error_code="GYMMATE_PRESIGN_S3_FAILURE",
                    log_data={"client_id": client_id, "exc": repr(exc)},
                )

            version = int(time.time() * 1000)
            results.append(PresignedPhotoUpload(
                url=presigned["url"],
                fields=presigned["fields"],
                key=key,
                cdn_url=build_cdn_url(key, version_ms=version),
                version=version,
                expires_in_seconds=PRESIGN_EXPIRES_SECONDS,
            ))
        return results

    # -------------------------------------------------------------------
    # Per-user prefix validator — called by service on Step 2 submission
    # -------------------------------------------------------------------
    @classmethod
    def expected_prefix_for(cls, client_id: int) -> str:
        return f"{cls.KEY_PREFIX}/{client_id}/"

    # -------------------------------------------------------------------
    # Internal validators
    # -------------------------------------------------------------------
    @staticmethod
    def _validate_content_type(content_type: str, client_id: int) -> None:
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise FittbotHTTPException(
                status_code=400,
                detail=f"Content type must be one of {sorted(ALLOWED_CONTENT_TYPES)}",
                error_code="GYMMATE_PRESIGN_BAD_CONTENT_TYPE",
                log_data={"client_id": client_id, "content_type": content_type},
            )

    @staticmethod
    def _validate_display_order(display_order: int, client_id: int) -> None:
        if not 0 <= display_order <= 2:
            raise FittbotHTTPException(
                status_code=400,
                detail="display_order must be 0, 1, or 2",
                error_code="GYMMATE_PRESIGN_BAD_ORDER",
                log_data={"client_id": client_id, "display_order": display_order},
            )


# ---------------------------------------------------------------------------
# Slot request shape (used by service & HTTP schema)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PresignSlotRequest:
    display_order: int
    content_type: str
