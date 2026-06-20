"""Tests for the S3 storage adapter — _storage.ProfilePhotoStorage.

Mocks `app.services.s3_service.generate_presigned_post` so no AWS calls
are made. Verifies:
  - Per-user key prefix
  - Content-type whitelist
  - Display-order range
  - Slot count limits
  - File extension picked from content-type
  - Error mapping (S3 failure → 503)
"""

from unittest.mock import patch

import pytest

from app.fittbot_api.v2.Fymble.gym_mate.profile._storage import (
    ALLOWED_CONTENT_TYPES,
    MAX_UPLOAD_BYTES,
    PRESIGN_EXPIRES_SECONDS,
    PresignSlotRequest,
    ProfilePhotoStorage,
    build_cdn_url,
)
from app.utils.logging_utils import FittbotHTTPException


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _fake_presign_response(key):
    return {
        "url": "https://fittbot-uploads.s3.ap-south-2.amazonaws.com/",
        "fields": {"key": key, "Content-Type": "image/jpeg"},
    }


@pytest.fixture
def storage():
    return ProfilePhotoStorage()


# ---------------------------------------------------------------------------
# Key shape
# ---------------------------------------------------------------------------
class TestKeyShape:
    def test_keys_use_per_user_prefix(self, storage):
        with patch(
            "app.fittbot_api.v2.Fymble.gym_mate.profile._storage.s3_service.generate_presigned_post",
            side_effect=lambda key, **_: _fake_presign_response(key),
        ):
            result = storage.presign_uploads(
                client_id=42,
                slots=[
                    PresignSlotRequest(display_order=0, content_type="image/jpeg"),
                    PresignSlotRequest(display_order=1, content_type="image/png"),
                ],
            )
        for env in result:
            assert env.key.startswith("gym_mate/profile/42/")
        assert ProfilePhotoStorage.expected_prefix_for(42) == "gym_mate/profile/42/"

    def test_different_users_get_different_prefixes(self, storage):
        with patch(
            "app.fittbot_api.v2.Fymble.gym_mate.profile._storage.s3_service.generate_presigned_post",
            side_effect=lambda key, **_: _fake_presign_response(key),
        ):
            for cid in (1, 999):
                r = storage.presign_uploads(
                    client_id=cid,
                    slots=[PresignSlotRequest(display_order=0, content_type="image/jpeg")],
                )
                assert r[0].key.startswith(f"gym_mate/profile/{cid}/")

    def test_extension_picked_from_content_type(self, storage):
        with patch(
            "app.fittbot_api.v2.Fymble.gym_mate.profile._storage.s3_service.generate_presigned_post",
            side_effect=lambda key, **_: _fake_presign_response(key),
        ):
            r = storage.presign_uploads(
                client_id=1,
                slots=[
                    PresignSlotRequest(display_order=0, content_type="image/jpeg"),
                    PresignSlotRequest(display_order=1, content_type="image/png"),
                    PresignSlotRequest(display_order=2, content_type="image/webp"),
                ],
            )
        assert r[0].key.endswith(".jpg")
        assert r[1].key.endswith(".png")
        assert r[2].key.endswith(".webp")

    def test_keys_are_unique_per_call(self, storage):
        """uuid in the filename should make collisions astronomical."""
        with patch(
            "app.fittbot_api.v2.Fymble.gym_mate.profile._storage.s3_service.generate_presigned_post",
            side_effect=lambda key, **_: _fake_presign_response(key),
        ):
            r1 = storage.presign_uploads(
                client_id=1,
                slots=[PresignSlotRequest(display_order=0, content_type="image/jpeg")],
            )
            r2 = storage.presign_uploads(
                client_id=1,
                slots=[PresignSlotRequest(display_order=0, content_type="image/jpeg")],
            )
        assert r1[0].key != r2[0].key


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
class TestValidation:
    def test_rejects_empty_slots(self, storage):
        with pytest.raises(FittbotHTTPException) as exc_info:
            storage.presign_uploads(client_id=1, slots=[])
        assert exc_info.value.error_code == "GYMMATE_PRESIGN_NO_SLOTS"

    def test_rejects_too_many_slots(self, storage):
        with pytest.raises(FittbotHTTPException) as exc_info:
            storage.presign_uploads(
                client_id=1,
                slots=[
                    PresignSlotRequest(display_order=i, content_type="image/jpeg")
                    for i in range(4)
                ],
            )
        assert exc_info.value.error_code == "GYMMATE_PRESIGN_TOO_MANY"

    def test_rejects_bad_content_type(self, storage):
        with pytest.raises(FittbotHTTPException) as exc_info:
            storage.presign_uploads(
                client_id=1,
                slots=[PresignSlotRequest(display_order=0, content_type="image/heic")],
            )
        assert exc_info.value.error_code == "GYMMATE_PRESIGN_BAD_CONTENT_TYPE"

    def test_rejects_bad_display_order(self, storage):
        with pytest.raises(FittbotHTTPException) as exc_info:
            storage.presign_uploads(
                client_id=1,
                slots=[PresignSlotRequest(display_order=5, content_type="image/jpeg")],
            )
        assert exc_info.value.error_code == "GYMMATE_PRESIGN_BAD_ORDER"

    def test_allowed_content_types_set_is_expected(self):
        assert ALLOWED_CONTENT_TYPES == frozenset({
            "image/jpeg", "image/png", "image/webp",
        })


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
class TestConstants:
    def test_max_upload_size_is_20mb(self):
        assert MAX_UPLOAD_BYTES == 20 * 1024 * 1024

    def test_expiry_matches_house_convention(self):
        assert PRESIGN_EXPIRES_SECONDS == 600


# ---------------------------------------------------------------------------
# CDN URL helper
# ---------------------------------------------------------------------------
class TestCdnUrlBuilder:
    def test_no_version_yields_plain_url(self):
        url = build_cdn_url("gym_mate/profile/42/0_xxx.jpg")
        assert url.startswith("https://")
        assert url.endswith("gym_mate/profile/42/0_xxx.jpg")
        assert "?v=" not in url

    def test_with_version_appends_cache_buster(self):
        url = build_cdn_url("gym_mate/profile/42/0_xxx.jpg", version_ms=1716892345000)
        assert url.endswith("?v=1716892345000")


# ---------------------------------------------------------------------------
# Presign response shape — cdn_url + version present
# ---------------------------------------------------------------------------
class TestPresignResponseShape:
    def test_envelope_includes_cdn_url_and_version(self, storage):
        with patch(
            "app.fittbot_api.v2.Fymble.gym_mate.profile._storage.s3_service.generate_presigned_post",
            side_effect=lambda key, **_: _fake_presign_response(key),
        ):
            r = storage.presign_uploads(
                client_id=42,
                slots=[PresignSlotRequest(display_order=0, content_type="image/jpeg")],
            )
        env = r[0]
        assert env.cdn_url.endswith(f"?v={env.version}")
        assert env.cdn_url.startswith("https://")
        assert env.key in env.cdn_url
        assert isinstance(env.version, int) and env.version > 0


# ---------------------------------------------------------------------------
# S3 failure mapping
# ---------------------------------------------------------------------------
class TestS3FailureHandling:
    def test_s3_exception_becomes_503(self, storage):
        with patch(
            "app.fittbot_api.v2.Fymble.gym_mate.profile._storage.s3_service.generate_presigned_post",
            side_effect=RuntimeError("boto3 ate the ocean"),
        ):
            with pytest.raises(FittbotHTTPException) as exc_info:
                storage.presign_uploads(
                    client_id=1,
                    slots=[PresignSlotRequest(display_order=0, content_type="image/jpeg")],
                )
        assert exc_info.value.status_code == 503
        assert exc_info.value.error_code == "GYMMATE_PRESIGN_S3_FAILURE"


# ---------------------------------------------------------------------------
# Presign params passed to s3_service
# ---------------------------------------------------------------------------
class TestS3CallParams:
    def test_passes_max_size_and_expiry(self, storage):
        captured = {}

        def capture(key, content_type, max_size, expires):
            captured["key"] = key
            captured["content_type"] = content_type
            captured["max_size"] = max_size
            captured["expires"] = expires
            return _fake_presign_response(key)

        with patch(
            "app.fittbot_api.v2.Fymble.gym_mate.profile._storage.s3_service.generate_presigned_post",
            side_effect=capture,
        ):
            storage.presign_uploads(
                client_id=1,
                slots=[PresignSlotRequest(display_order=0, content_type="image/png")],
            )

        assert captured["content_type"] == "image/png"
        assert captured["max_size"] == MAX_UPLOAD_BYTES
        assert captured["expires"] == PRESIGN_EXPIRES_SECONDS
        assert captured["key"].startswith("gym_mate/profile/1/0_")
