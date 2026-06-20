"""Service-layer tests for gym_mate.profile._service.

Uses fake repository + fake cache + recording event bus + stub storage —
no real DB, no real Redis, no real S3. Verifies orchestration, cache
behaviour, event publishing, and the per-user S3 prefix security check.
"""

from typing import List, Optional

import pytest

from app.fittbot_api.v2.Fymble.gym_mate.profile import _domain as d
from app.fittbot_api.v2.Fymble.gym_mate.profile._events import ProfileOnboarded
from app.fittbot_api.v2.Fymble.gym_mate.profile._service import ProfileService
from app.fittbot_api.v2.Fymble.gym_mate.profile._storage import (
    PresignSlotRequest,
    PresignedPhotoUpload,
    ProfilePhotoStorage,
)
from app.fittbot_api.v2.Fymble.gym_mate.profile import schemas as dto
from app.utils.logging_utils import FittbotHTTPException


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------
class InMemoryProfileRepository:
    """Stand-in for the SQLAlchemy repository."""

    def __init__(self):
        self._by_client: dict[int, d.Profile] = {}
        self._next_id = 1
        # Configurable per-client social counts + names for tests.
        self.friends_counts: dict[int, int] = {}
        self.pending_received_counts: dict[int, int] = {}
        self.names: dict[int, str] = {}

    async def get_client_name(self, client_id: int):
        return self.names.get(client_id)

    async def get_by_client_id(self, client_id: int) -> Optional[d.Profile]:
        return self._by_client.get(client_id)

    async def save(self, profile: d.Profile) -> d.Profile:
        if profile.id is None:
            profile.id = self._next_id
            self._next_id += 1
        self._by_client[profile.client_id] = profile
        return profile

    # --- social counts (drive profile header badges) ---
    async def count_friends(self, client_id: int) -> int:
        return self.friends_counts.get(client_id, 0)

    async def count_pending_received_requests(self, client_id: int) -> int:
        return self.pending_received_counts.get(client_id, 0)

    # --- lite methods used by service hot paths ---
    async def get_status_lite(self, client_id: int):
        p = self._by_client.get(client_id)
        return (p.id, p.onboarding_completed) if p else None

    async def get_match_attrs_lite(self, client_id: int):
        p = self._by_client.get(client_id)
        if p is None:
            return None
        return {
            "primary_goal": p.primary_goal.value,
            "activity_interests": p.activity_interests.as_list(),
            "preferred_timing": p.preferred_timing.value,
            "gym_personality": p.gym_personality.value,
        }

    async def get_summary_lite(self, client_id: int):
        p = self._by_client.get(client_id)
        if p is None:
            return None
        primary = next((ph for ph in p.photos if ph.is_primary), None)
        return {
            "bio": p.bio.value if p.bio else None,
            "onboarding_completed": p.onboarding_completed,
            "primary_photo_s3_path": primary.s3_path if primary else None,
        }


class RecordingEventBus:
    def __init__(self):
        self.published: list = []

    async def publish(self, event: object) -> None:
        self.published.append(event)


class InMemoryCache:
    """Fake ProfileCache — same interface, dict-backed."""

    def __init__(self):
        self.status: dict[int, dto.OnboardingStatusDTO] = {}
        self.match_attrs: dict[int, dto.ProfileMatchAttributesDTO] = {}
        self.summary: dict[int, dto.ProfileSummaryDTO] = {}
        self.invalidations = 0

    async def get_status(self, client_id):
        return self.status.get(client_id)

    async def set_status(self, dto_):
        self.status[dto_.client_id] = dto_

    async def get_match_attrs(self, client_id):
        return self.match_attrs.get(client_id)

    async def set_match_attrs(self, dto_):
        self.match_attrs[dto_.client_id] = dto_

    async def get_summary(self, client_id):
        return self.summary.get(client_id)

    async def set_summary(self, dto_):
        self.summary[dto_.client_id] = dto_

    async def invalidate(self, client_id):
        self.invalidations += 1
        self.status.pop(client_id, None)
        self.match_attrs.pop(client_id, None)
        self.summary.pop(client_id, None)


class StubStorage(ProfilePhotoStorage):
    """Inherits real validators; overrides S3 call to avoid AWS."""

    def __init__(self):
        self.calls: list = []

    def presign_uploads(self, client_id, slots):
        if not slots or len(slots) > 3:
            raise FittbotHTTPException(
                status_code=400, detail="bad count",
                error_code="STUB", log_data={},
            )
        results = []
        for s in slots:
            self._validate_content_type(s.content_type, client_id)
            self._validate_display_order(s.display_order, client_id)
            ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[s.content_type]
            key = f"gym_mate/profile/{client_id}/{s.display_order}_stub.{ext}"
            results.append(PresignedPhotoUpload(
                url=f"https://stub.s3/{key}",
                fields={"Content-Type": s.content_type},
                key=key,
                cdn_url=f"https://stub.s3/{key}?v=1",
                version=1,
                expires_in_seconds=600,
            ))
        self.calls.append((client_id, list(slots)))
        return results


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def repo(): return InMemoryProfileRepository()

@pytest.fixture
def bus(): return RecordingEventBus()

@pytest.fixture
def cache(): return InMemoryCache()

@pytest.fixture
def storage(): return StubStorage()

@pytest.fixture
def service(repo, bus, cache, storage):
    return ProfileService(
        repository=repo, event_bus=bus, cache=cache, storage=storage
    )


def _photo_key(client_id, slot):
    return f"gym_mate/profile/{client_id}/{slot}_abc.jpg"


# ---------------------------------------------------------------------------
# Step 1
# ---------------------------------------------------------------------------
class TestSubmitStep1:
    @pytest.mark.asyncio
    async def test_first_submit_creates_profile(self, service, repo):
        status = await service.submit_step1(
            client_id=42,
            primary_goal="Muscle Building",
            activity_interests=["Weightlifting", "HIIT"],
            preferred_timing="Morning",
            gym_personality="Serious & Focused",
        )
        assert status.next_step == 2
        assert status.onboarding_completed is False
        stored = await repo.get_by_client_id(42)
        assert stored.primary_goal is d.PrimaryGoal.MUSCLE_BUILDING
        assert stored.activity_interests.as_list() == ["Weightlifting", "HIIT"]

    @pytest.mark.asyncio
    async def test_step1_invalidates_cache(self, service, cache):
        cache.status[42] = dto.OnboardingStatusDTO(
            client_id=42, next_step=2, onboarding_completed=False
        )
        await service.submit_step1(
            client_id=42, primary_goal="Muscle Building",
            activity_interests=["Weightlifting"], preferred_timing="Morning",
            gym_personality="Serious & Focused",
        )
        assert cache.invalidations == 1
        assert 42 not in cache.status

    @pytest.mark.asyncio
    async def test_invalid_goal_rejected(self, service):
        with pytest.raises(FittbotHTTPException) as exc_info:
            await service.submit_step1(
                client_id=1, primary_goal="time_travel",
                activity_interests=["Cardio"], preferred_timing="Morning",
                gym_personality="Serious & Focused",
            )
        assert exc_info.value.status_code == 400
        assert exc_info.value.error_code == "GYMMATE_PROFILE_STEP1_INVALID"

    @pytest.mark.asyncio
    async def test_unknown_activity_rejected(self, service):
        with pytest.raises(FittbotHTTPException):
            await service.submit_step1(
                client_id=1, primary_goal="Muscle Building",
                activity_interests=["badminton"], preferred_timing="Morning",
                gym_personality="Serious & Focused",
            )


# ---------------------------------------------------------------------------
# Step 2
# ---------------------------------------------------------------------------
class TestSubmitStep2:
    async def _seed(self, service, client_id=42):
        await service.submit_step1(
            client_id=client_id, primary_goal="Improve Endurance",
            activity_interests=["Running"], preferred_timing="Morning",
            gym_personality="Competitive",
        )

    @pytest.mark.asyncio
    async def test_step2_completes_onboarding(self, service, repo):
        await self._seed(service)
        status = await service.submit_step2(
            client_id=42,
            photo_paths=[_photo_key(42, 0), _photo_key(42, 1)],
            bio="Marathon training partner wanted",
        )
        assert status.onboarding_completed is True
        assert status.next_step == 0
        stored = await repo.get_by_client_id(42)
        assert len(stored.photos) == 2
        assert stored.photos[0].is_primary is True

    @pytest.mark.asyncio
    async def test_step2_publishes_event_on_first_completion(self, service, bus):
        await self._seed(service)
        await service.submit_step2(
            client_id=42, photo_paths=[_photo_key(42, 0)], bio=None
        )
        assert len(bus.published) == 1
        evt = bus.published[0]
        assert isinstance(evt, ProfileOnboarded)
        assert evt.client_id == 42

    @pytest.mark.asyncio
    async def test_step2_resubmit_does_not_republish_event(self, service, bus):
        await self._seed(service)
        await service.submit_step2(
            client_id=42, photo_paths=[_photo_key(42, 0)], bio=None
        )
        await service.submit_step2(
            client_id=42,
            photo_paths=[_photo_key(42, 0), _photo_key(42, 1)],
            bio="new bio",
        )
        assert len(bus.published) == 1

    @pytest.mark.asyncio
    async def test_step2_invalidates_cache(self, service, cache):
        await self._seed(service)
        cache.invalidations = 0
        await service.submit_step2(
            client_id=42, photo_paths=[_photo_key(42, 0)], bio=None
        )
        assert cache.invalidations == 1

    @pytest.mark.asyncio
    async def test_step2_before_step1_rejected(self, service):
        with pytest.raises(FittbotHTTPException) as exc_info:
            await service.submit_step2(
                client_id=99, photo_paths=[_photo_key(99, 0)], bio=None
            )
        assert exc_info.value.status_code == 400
        assert exc_info.value.error_code == "GYMMATE_PROFILE_STEP2_BEFORE_STEP1"

    @pytest.mark.asyncio
    async def test_step2_rejects_foreign_photo_key(self, service):
        """Security: user 42 cannot submit a key under user 99's prefix."""
        await self._seed(service, client_id=42)
        with pytest.raises(FittbotHTTPException) as exc_info:
            await service.submit_step2(
                client_id=42,
                photo_paths=[_photo_key(99, 0)],   # other user's prefix
                bio=None,
            )
        assert exc_info.value.status_code == 400
        assert exc_info.value.error_code == "GYMMATE_PROFILE_STEP2_FOREIGN_KEY"

    @pytest.mark.asyncio
    async def test_step2_rejects_arbitrary_path(self, service):
        await self._seed(service, client_id=42)
        with pytest.raises(FittbotHTTPException) as exc_info:
            await service.submit_step2(
                client_id=42,
                photo_paths=["post_uploads/random.jpg"],
                bio=None,
            )
        assert exc_info.value.error_code == "GYMMATE_PROFILE_STEP2_FOREIGN_KEY"

    @pytest.mark.asyncio
    async def test_step2_bio_over_limit_rejected(self, service):
        await self._seed(service, client_id=42)
        with pytest.raises(FittbotHTTPException):
            await service.submit_step2(
                client_id=42, photo_paths=[_photo_key(42, 0)], bio="x" * 301
            )

    @pytest.mark.asyncio
    async def test_step2_empty_body_just_flips_flag(self, service, repo, bus):
        """No photos, no bio → only onboarding_completed flips."""
        await self._seed(service, client_id=42)

        status = await service.submit_step2(
            client_id=42, photo_paths=None, bio=None,
        )
        assert status.onboarding_completed is True
        assert status.next_step == 0

        stored = await repo.get_by_client_id(42)
        assert stored.onboarding_completed is True
        assert stored.photos == []
        assert stored.bio is None
        # First-time completion still fires ProfileOnboarded
        assert len(bus.published) == 1

    @pytest.mark.asyncio
    async def test_step2_empty_list_is_same_as_none(self, service, repo):
        await self._seed(service, client_id=42)
        await service.submit_step2(
            client_id=42, photo_paths=[], bio=None,
        )
        stored = await repo.get_by_client_id(42)
        assert stored.onboarding_completed is True
        assert stored.photos == []

    @pytest.mark.asyncio
    async def test_step2_only_bio(self, service, repo):
        await self._seed(service, client_id=42)
        await service.submit_step2(
            client_id=42, photo_paths=None, bio="bio only",
        )
        stored = await repo.get_by_client_id(42)
        assert stored.onboarding_completed is True
        assert stored.photos == []
        assert stored.bio.value == "bio only"

    @pytest.mark.asyncio
    async def test_step2_only_photos(self, service, repo):
        await self._seed(service, client_id=42)
        await service.submit_step2(
            client_id=42, photo_paths=[_photo_key(42, 0)], bio=None,
        )
        stored = await repo.get_by_client_id(42)
        assert stored.onboarding_completed is True
        assert len(stored.photos) == 1
        assert stored.bio is None

    @pytest.mark.asyncio
    async def test_step2_resubmit_with_empty_preserves_existing(self, service, repo):
        """Once a user has photos + bio, resubmitting with empty body
        must NOT wipe them."""
        await self._seed(service, client_id=42)
        await service.submit_step2(
            client_id=42,
            photo_paths=[_photo_key(42, 0), _photo_key(42, 1)],
            bio="my real bio",
        )

        await service.submit_step2(
            client_id=42, photo_paths=None, bio=None,
        )

        stored = await repo.get_by_client_id(42)
        assert len(stored.photos) == 2
        assert stored.bio.value == "my real bio"


# ---------------------------------------------------------------------------
# Presign
# ---------------------------------------------------------------------------
class TestPresign:
    @pytest.mark.asyncio
    async def test_presign_returns_one_envelope_per_slot(self, service):
        slots = [
            PresignSlotRequest(display_order=0, content_type="image/jpeg"),
            PresignSlotRequest(display_order=1, content_type="image/png"),
        ]
        result = await service.presign_photos(client_id=42, slots=slots)
        assert len(result) == 2
        for env in result:
            assert env.key.startswith("gym_mate/profile/42/")
            assert env.expires_in_seconds == 600
            assert env.cdn_url and "?v=" in env.cdn_url
            assert env.version > 0

    @pytest.mark.asyncio
    async def test_presign_rejects_bad_content_type(self, service):
        slots = [PresignSlotRequest(display_order=0, content_type="image/heic")]
        with pytest.raises(FittbotHTTPException) as exc_info:
            await service.presign_photos(client_id=42, slots=slots)
        assert exc_info.value.error_code == "GYMMATE_PRESIGN_BAD_CONTENT_TYPE"

    @pytest.mark.asyncio
    async def test_presign_keys_belong_to_caller(self, service):
        slots = [PresignSlotRequest(display_order=0, content_type="image/jpeg")]
        result = await service.presign_photos(client_id=99, slots=slots)
        # Key under user 99's prefix only — not 42's
        assert result[0].key.startswith("gym_mate/profile/99/")
        assert "/42/" not in result[0].key


# ---------------------------------------------------------------------------
# Status — cache-first behaviour
# ---------------------------------------------------------------------------
class TestStatusCaching:
    @pytest.mark.asyncio
    async def test_first_status_call_populates_cache(self, service, cache):
        assert cache.status.get(1) is None
        s1 = await service.get_status(1)
        assert cache.status.get(1) is not None
        assert s1.next_step == 1
        assert s1.onboarding_completed is False

    @pytest.mark.asyncio
    async def test_second_status_call_hits_cache(self, service, cache):
        # Seed cache with a different value than DB would return
        cache.status[1] = dto.OnboardingStatusDTO(
            client_id=1, next_step=0, onboarding_completed=True
        )
        s = await service.get_status(1)
        assert s.onboarding_completed is True  # came from cache, not DB

    @pytest.mark.asyncio
    async def test_status_reflects_step1(self, service):
        await service.submit_step1(
            client_id=1, primary_goal="Muscle Building",
            activity_interests=["Weightlifting"], preferred_timing="Morning",
            gym_personality="Serious & Focused",
        )
        s = await service.get_status(1)
        assert s.next_step == 2
        assert s.onboarding_completed is False

    @pytest.mark.asyncio
    async def test_status_reflects_step2(self, service):
        await service.submit_step1(
            client_id=1, primary_goal="Muscle Building",
            activity_interests=["Weightlifting"], preferred_timing="Morning",
            gym_personality="Serious & Focused",
        )
        await service.submit_step2(
            client_id=1, photo_paths=[_photo_key(1, 0)], bio=None
        )
        s = await service.get_status(1)
        assert s.next_step == 0
        assert s.onboarding_completed is True


# ---------------------------------------------------------------------------
# Cross-module port methods
# ---------------------------------------------------------------------------
class TestPortMethods:
    @pytest.mark.asyncio
    async def test_is_onboarded_false_when_no_profile(self, service):
        assert await service.is_onboarded(999) is False

    @pytest.mark.asyncio
    async def test_is_onboarded_true_after_step2(self, service):
        await service.submit_step1(
            client_id=1, primary_goal="Muscle Building",
            activity_interests=["Weightlifting"], preferred_timing="Morning",
            gym_personality="Serious & Focused",
        )
        await service.submit_step2(
            client_id=1, photo_paths=[_photo_key(1, 0)], bio=None
        )
        assert await service.is_onboarded(1) is True

    @pytest.mark.asyncio
    async def test_get_match_attrs_caches_result(self, service, cache):
        await service.submit_step1(
            client_id=1, primary_goal="Improve Endurance",
            activity_interests=["Running"], preferred_timing="Morning",
            gym_personality="Competitive",
        )
        a1 = await service.get_match_attributes(1)
        assert a1 is not None
        assert cache.match_attrs.get(1) is not None
        # Second call hits cache
        a2 = await service.get_match_attributes(1)
        assert a2.primary_goal == "Improve Endurance"

    @pytest.mark.asyncio
    async def test_get_summary_returns_primary_photo(self, service):
        await service.submit_step1(
            client_id=1, primary_goal="Muscle Building",
            activity_interests=["Weightlifting"], preferred_timing="Morning",
            gym_personality="Serious & Focused",
        )
        await service.submit_step2(
            client_id=1,
            photo_paths=[_photo_key(1, 0), _photo_key(1, 1)],
            bio="My bio",
        )
        summary = await service.get_summary(1)
        # primary_photo_url is now a CDN URL containing the key
        assert summary.primary_photo_url is not None
        assert _photo_key(1, 0) in summary.primary_photo_url
        assert summary.primary_photo_url.startswith("https://")
        assert summary.bio == "My bio"
        assert summary.onboarding_completed is True

    @pytest.mark.asyncio
    async def test_get_summary_none_when_no_profile(self, service):
        assert await service.get_summary(999) is None


# ---------------------------------------------------------------------------
# Full profile read
# ---------------------------------------------------------------------------
class TestFullProfile:
    @pytest.mark.asyncio
    async def test_full_profile_not_found(self, service):
        with pytest.raises(FittbotHTTPException) as exc_info:
            await service.get_full_profile(999)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_full_profile_includes_social_counts(self, service, repo):
        await service.submit_step1(
            client_id=1, primary_goal="Muscle Building",
            activity_interests=["Weightlifting"], preferred_timing="Morning",
            gym_personality="Serious & Focused",
        )
        repo.friends_counts[1] = 12
        repo.pending_received_counts[1] = 3

        profile = await service.get_full_profile(1)
        assert profile.social.friends_count == 12
        assert profile.social.pending_received_requests_count == 3

    @pytest.mark.asyncio
    async def test_full_profile_includes_name_from_clients_table(self, service, repo):
        await service.submit_step1(
            client_id=1, primary_goal="Muscle Building",
            activity_interests=["Weightlifting"], preferred_timing="Morning",
            gym_personality="Serious & Focused",
        )
        repo.names[1] = "Raj M.K"

        profile = await service.get_full_profile(1)
        assert profile.name == "Raj M.K"

    @pytest.mark.asyncio
    async def test_full_profile_name_none_when_client_has_no_name(self, service):
        await service.submit_step1(
            client_id=1, primary_goal="Muscle Building",
            activity_interests=["Weightlifting"], preferred_timing="Morning",
            gym_personality="Serious & Focused",
        )
        # repo.names[1] not set → returns None
        profile = await service.get_full_profile(1)
        assert profile.name is None

    @pytest.mark.asyncio
    async def test_edit_profile_response_includes_name(self, service, repo):
        await service.submit_step1(
            client_id=1, primary_goal="Muscle Building",
            activity_interests=["Weightlifting"], preferred_timing="Morning",
            gym_personality="Serious & Focused",
        )
        repo.names[1] = "Raj M.K"
        result = await service.edit_profile(client_id=1, bio="x")
        assert result.name == "Raj M.K"

    @pytest.mark.asyncio
    async def test_full_profile_zero_counts_by_default(self, service):
        await service.submit_step1(
            client_id=1, primary_goal="Muscle Building",
            activity_interests=["Weightlifting"], preferred_timing="Morning",
            gym_personality="Serious & Focused",
        )
        profile = await service.get_full_profile(1)
        assert profile.social.friends_count == 0
        assert profile.social.pending_received_requests_count == 0

    @pytest.mark.asyncio
    async def test_full_profile_after_complete(self, service):
        await service.submit_step1(
            client_id=1, primary_goal="Muscle Building",
            activity_interests=["Weightlifting", "HIIT"], preferred_timing="Morning",
            gym_personality="Serious & Focused",
        )
        await service.submit_step2(
            client_id=1,
            photo_paths=[_photo_key(1, 0), _photo_key(1, 1)],
            bio="hello",
        )
        profile = await service.get_full_profile(1)
        assert profile.client_id == 1
        assert profile.bio == "hello"
        assert len(profile.photos) == 2
        assert profile.photos[0].is_primary is True
        # Each photo carries both raw key and ready-to-render CDN URL
        for p in profile.photos:
            assert p.s3_path.startswith("gym_mate/profile/1/")
            assert p.cdn_url.startswith("https://")
            assert p.s3_path in p.cdn_url
        assert profile.onboarding_completed is True


# ---------------------------------------------------------------------------
# Edit profile — partial PATCH-style update of the 4 Step-1 fields + bio + photos
# ---------------------------------------------------------------------------
class TestEditProfile:
    async def _seed(self, service, client_id=42):
        await service.submit_step1(
            client_id=client_id, primary_goal="Muscle Building",
            activity_interests=["Weightlifting"], preferred_timing="Morning",
            gym_personality="Serious & Focused",
        )

    @pytest.mark.asyncio
    async def test_edit_only_bio(self, service, repo):
        await self._seed(service)
        result = await service.edit_profile(client_id=42, bio="Just updated")
        assert result.bio == "Just updated"
        stored = await repo.get_by_client_id(42)
        # Step-1 fields untouched
        assert stored.primary_goal is d.PrimaryGoal.MUSCLE_BUILDING

    @pytest.mark.asyncio
    async def test_edit_only_goal(self, service, repo):
        await self._seed(service)
        result = await service.edit_profile(client_id=42, primary_goal="Weight Loss")
        assert result.primary_goal == "Weight Loss"
        stored = await repo.get_by_client_id(42)
        # Other Step-1 fields untouched
        assert stored.preferred_timing is d.PreferredTiming.MORNING

    @pytest.mark.asyncio
    async def test_edit_all_fields_at_once(self, service):
        await self._seed(service)
        result = await service.edit_profile(
            client_id=42,
            primary_goal="Weight Loss",
            activity_interests=["Yoga", "Cardio"],
            preferred_timing="Evening",
            gym_personality="Chill & Relaxed",
            bio="New me",
            photo_paths=[_photo_key(42, 0), _photo_key(42, 1)],
        )
        assert result.primary_goal == "Weight Loss"
        assert result.activity_interests == ["Yoga", "Cardio"]
        assert result.preferred_timing == "Evening"
        assert result.gym_personality == "Chill & Relaxed"
        # `details` is the chip-row mirror of the four fields above
        assert result.details == [
            "Weight Loss",
            "Yoga", "Cardio",
            "Evening",
            "Chill & Relaxed",
        ]
        assert result.bio == "New me"
        assert len(result.photos) == 2
        assert result.photos[0].is_primary is True

    @pytest.mark.asyncio
    async def test_edit_invalid_goal_rejected(self, service):
        await self._seed(service)
        with pytest.raises(FittbotHTTPException) as exc_info:
            await service.edit_profile(client_id=42, primary_goal="time_travel")
        assert exc_info.value.error_code == "GYMMATE_PROFILE_EDIT_INVALID"

    @pytest.mark.asyncio
    async def test_edit_rejects_foreign_photo_key(self, service):
        await self._seed(service)
        with pytest.raises(FittbotHTTPException) as exc_info:
            await service.edit_profile(
                client_id=42, photo_paths=[_photo_key(99, 0)],
            )
        assert exc_info.value.error_code == "GYMMATE_PROFILE_EDIT_FOREIGN_KEY"

    @pytest.mark.asyncio
    async def test_edit_no_profile_returns_404(self, service):
        with pytest.raises(FittbotHTTPException) as exc_info:
            await service.edit_profile(client_id=999, bio="hi")
        assert exc_info.value.status_code == 404
        assert exc_info.value.error_code == "GYMMATE_PROFILE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_edit_invalidates_cache(self, service, cache):
        await self._seed(service)
        cache.invalidations = 0
        await service.edit_profile(client_id=42, bio="new")
        assert cache.invalidations == 1

    @pytest.mark.asyncio
    async def test_edit_response_includes_social_counts(self, service, repo):
        await self._seed(service)
        repo.friends_counts[42] = 7
        repo.pending_received_counts[42] = 2

        result = await service.edit_profile(client_id=42, bio="x")
        assert result.social.friends_count == 7
        assert result.social.pending_received_requests_count == 2

    @pytest.mark.asyncio
    async def test_edit_empty_body_is_noop_but_returns_profile(self, service, repo):
        await self._seed(service)
        result = await service.edit_profile(client_id=42)
        # Profile returned unchanged
        assert result.primary_goal == "Muscle Building"
        stored = await repo.get_by_client_id(42)
        assert stored.bio is None
