import time
import uuid
from dataclasses import dataclass
from typing import List, Optional

from app.services import s3_service
from app.utils.logging_utils import FittbotHTTPException


ALLOWED_CONTENT_TYPES = frozenset({
    "image/jpeg",
    "image/png",
    "image/webp",
})

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB — story images may be larger than profile pics
PRESIGN_EXPIRES_SECONDS = 600

_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


def build_cdn_url(key: str, version_ms: Optional[int] = None) -> str:
    # Pass-through for values that are already full URLs (test fixtures,
    # third-party hosted media, etc.). Avoids accidentally double-prefixing.
    if key and (key.startswith("http://") or key.startswith("https://")):
        return key
    base = f"https://{s3_service.BUCKET_NAME}.s3.{s3_service.AWS_REGION}.amazonaws.com"
    if version_ms is not None:
        return f"{base}/{key}?v={version_ms}"
    return f"{base}/{key}"


@dataclass(frozen=True)
class PresignedStoryUpload:
    url: str
    fields: dict
    key: str
    cdn_url: str
    version: int


class StoryMediaStorage:
    KEY_PREFIX = "gym_mate/stories"

    def presign_upload(
        self,
        client_id: int,
        content_type: str,
    ) -> PresignedStoryUpload:
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise FittbotHTTPException(
                status_code=400,
                detail=f"Content type must be one of {sorted(ALLOWED_CONTENT_TYPES)}",
                error_code="GYMMATE_STORY_BAD_CONTENT_TYPE",
                log_data={"client_id": client_id, "content_type": content_type},
            )

        ext = _EXT[content_type]
        key = f"{self.KEY_PREFIX}/{client_id}/{uuid.uuid4().hex}.{ext}"

        try:
            presigned = s3_service.generate_presigned_post(
                key=key,
                content_type=content_type,
                max_size=MAX_UPLOAD_BYTES,
                expires=PRESIGN_EXPIRES_SECONDS,
            )
        except Exception as exc:  # pragma: no cover
            raise FittbotHTTPException(
                status_code=503,
                detail="Could not generate upload URL, please retry",
                error_code="GYMMATE_STORY_PRESIGN_S3_FAILURE",
                log_data={"client_id": client_id, "exc": repr(exc)},
            )

        version = int(time.time() * 1000)
        return PresignedStoryUpload(
            url=presigned["url"],
            fields=presigned["fields"],
            key=key,
            cdn_url=build_cdn_url(key, version_ms=version),
            version=version,
        )

    @classmethod
    def expected_prefix_for(cls, client_id: int) -> str:
        return f"{cls.KEY_PREFIX}/{client_id}/"
