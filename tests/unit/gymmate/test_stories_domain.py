from datetime import datetime, timedelta

import pytest

from app.fittbot_api.v2.Fymble.gym_mate.stories import _domain as d


def _key(client_id, name="abc.jpg"):
    return d.S3MediaKey(f"gym_mate/stories/{client_id}/{name}")


class TestS3MediaKey:
    def test_accepts_valid_key(self):
        k = d.S3MediaKey("gym_mate/stories/42/abc.jpg")
        assert k.value == "gym_mate/stories/42/abc.jpg"

    def test_rejects_wrong_prefix(self):
        with pytest.raises(d.InvalidStoryMediaKey):
            d.S3MediaKey("post_uploads/abc.jpg")

    def test_rejects_empty(self):
        with pytest.raises(d.InvalidStoryMediaKey):
            d.S3MediaKey("")

    def test_rejects_too_long(self):
        with pytest.raises(d.InvalidStoryMediaKey):
            d.S3MediaKey("gym_mate/stories/42/" + ("x" * 500))


class TestStoryCaption:
    def test_strips(self):
        assert d.StoryCaption("  hi  ").value == "hi"

    def test_max_length_ok(self):
        d.StoryCaption("x" * 300)

    def test_over_max_rejected(self):
        with pytest.raises(d.InvalidCaption):
            d.StoryCaption("x" * 301)


class TestStoryPublish:
    def test_ttl_is_24h(self):
        now = datetime(2026, 5, 22, 10, 0)
        s = d.Story.publish(
            client_id=42,
            s3_key=_key(42),
            now=now,
        )
        assert s.created_at == now
        assert s.expires_at == now + timedelta(hours=24)
        assert s.is_deleted is False
        assert s.id is None

    def test_defaults(self):
        s = d.Story.publish(client_id=42, s3_key=_key(42))
        assert s.media_type is d.StoryMediaType.IMAGE
        assert s.audience is d.StoryAudience.PUBLIC
        assert s.caption is None

    def test_is_active_when_fresh(self):
        s = d.Story.publish(client_id=42, s3_key=_key(42))
        assert s.is_active() is True

    def test_not_active_after_expiry(self):
        from app.utils.time_utils import utc_now
        old = utc_now() - timedelta(hours=25)
        s = d.Story.publish(client_id=42, s3_key=_key(42), now=old)
        assert s.is_active() is False


class TestStoryDelete:
    def _live(self, client_id=42):
        return d.Story.publish(client_id=client_id, s3_key=_key(client_id))

    def test_owner_can_delete(self):
        s = self._live(42)
        s.delete_by(42)
        assert s.is_deleted is True
        assert s.deleted_at is not None

    def test_non_owner_cannot_delete(self):
        s = self._live(42)
        with pytest.raises(d.StoryNotOwned):
            s.delete_by(99)

    def test_double_delete_rejected(self):
        s = self._live(42)
        s.delete_by(42)
        with pytest.raises(d.StoryAlreadyDeleted):
            s.delete_by(42)

    def test_deleted_is_not_active(self):
        s = self._live(42)
        s.delete_by(42)
        assert s.is_active() is False
