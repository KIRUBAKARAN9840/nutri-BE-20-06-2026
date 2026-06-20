"""Pure-domain tests for gym_mate.profile._domain.

These tests touch no DB, no Redis, no HTTP, no event bus. They cover:
  - Value object construction & validation (Bio, ActivityInterests, PhotoSlot)
  - Profile aggregate invariants (Step 1 → Step 2 ordering, photo limits,
    primary-photo rule, onboarding completion)
"""

import pytest

from app.fittbot_api.v2.Fymble.gym_mate.profile import _domain as d


# ---------------------------------------------------------------------------
# Bio
# ---------------------------------------------------------------------------
class TestBio:
    def test_strips_whitespace(self):
        assert d.Bio("  hello  ").value == "hello"

    def test_accepts_max_length(self):
        text = "x" * 300
        assert d.Bio(text).value == text

    def test_rejects_over_max_length(self):
        with pytest.raises(d.InvalidBio):
            d.Bio("x" * 301)

    def test_strip_then_check_length(self):
        assert d.Bio("     " + "x" * 300).value == "x" * 300

    def test_is_frozen(self):
        bio = d.Bio("hello")
        with pytest.raises(Exception):
            bio.value = "tampered"


# ---------------------------------------------------------------------------
# ActivityInterests
# ---------------------------------------------------------------------------
class TestActivityInterests:
    def test_accepts_known_values(self):
        ai = d.ActivityInterests(("Cardio", "Yoga"))
        assert ai.as_list() == ["Cardio", "Yoga"]

    def test_dedupes_preserving_order(self):
        ai = d.ActivityInterests(("Cardio", "Yoga", "Cardio"))
        assert ai.as_list() == ["Cardio", "Yoga"]

    def test_rejects_unknown_value(self):
        with pytest.raises(d.InvalidActivityInterests):
            d.ActivityInterests(("Cardio", "Underwater Basket Weaving"))

    def test_rejects_empty(self):
        with pytest.raises(d.InvalidActivityInterests):
            d.ActivityInterests(())

    def test_allows_many(self):
        # No upper cap — any number of known values is accepted.
        many = ("Cardio", "Yoga", "Running", "Weightlifting",
                "HIIT", "CrossFit", "Pilates")
        ai = d.ActivityInterests(many)
        assert ai.as_list() == list(many)

    def test_case_sensitive(self):
        # "cardio" lowercase is NOT a known value
        with pytest.raises(d.InvalidActivityInterests):
            d.ActivityInterests(("cardio",))


# ---------------------------------------------------------------------------
# PhotoSlot — keys must live under gym_mate/profile/ prefix
# ---------------------------------------------------------------------------
class TestPhotoSlot:
    VALID_KEY = "gym_mate/profile/42/0_abc.jpg"

    def test_construct_valid_photo(self):
        p = d.PhotoSlot(s3_path=self.VALID_KEY, display_order=0)
        assert p.display_order == 0
        assert p.is_primary is True

    def test_non_zero_order_is_not_primary(self):
        p = d.PhotoSlot(s3_path="gym_mate/profile/42/1_x.jpg", display_order=1)
        assert p.is_primary is False

    def test_rejects_wrong_prefix(self):
        with pytest.raises(d.InvalidPhoto):
            d.PhotoSlot(s3_path="post_uploads/abc.jpg", display_order=0)

    def test_rejects_s3_uri_format(self):
        # We store keys, not s3:// URIs
        with pytest.raises(d.InvalidPhoto):
            d.PhotoSlot(s3_path="s3://bucket/gym_mate/profile/42/0.jpg",
                        display_order=0)

    def test_rejects_empty_path(self):
        with pytest.raises(d.InvalidPhoto):
            d.PhotoSlot(s3_path="", display_order=0)

    def test_rejects_too_long_path(self):
        long_path = "gym_mate/profile/42/" + ("x" * 500)
        with pytest.raises(d.InvalidPhoto):
            d.PhotoSlot(s3_path=long_path, display_order=0)

    def test_rejects_order_out_of_range(self):
        with pytest.raises(d.InvalidPhoto):
            d.PhotoSlot(s3_path=self.VALID_KEY, display_order=3)
        with pytest.raises(d.InvalidPhoto):
            d.PhotoSlot(s3_path=self.VALID_KEY, display_order=-1)


# ---------------------------------------------------------------------------
# Profile aggregate — Step 1
# ---------------------------------------------------------------------------
def _interests(*v):
    return d.ActivityInterests(tuple(v))


def _photo(client_id, slot):
    return d.PhotoSlot(
        s3_path=f"gym_mate/profile/{client_id}/{slot}_abc.jpg",
        display_order=slot,
    )


class TestProfileStart:
    def _valid_start(self):
        return d.Profile.start(
            client_id=42,
            primary_goal=d.PrimaryGoal.MUSCLE_BUILDING,
            activity_interests=_interests("Weightlifting", "HIIT"),
            preferred_timing=d.PreferredTiming.MORNING,
            gym_personality=d.GymPersonality.SERIOUS_FOCUSED,
        )

    def test_start_creates_incomplete_profile(self):
        p = self._valid_start()
        assert p.client_id == 42
        assert p.onboarding_completed is False
        assert p.photos == []
        assert p.bio is None
        assert p.id is None

    def test_update_step1_overrides_fields(self):
        p = self._valid_start()
        p.update_step1(
            primary_goal=d.PrimaryGoal.WEIGHT_LOSS,
            activity_interests=_interests("Cardio"),
            preferred_timing=d.PreferredTiming.EVENING,
            gym_personality=d.GymPersonality.CHILL_RELAXED,
        )
        assert p.primary_goal is d.PrimaryGoal.WEIGHT_LOSS
        assert p.activity_interests.as_list() == ["Cardio"]
        assert p.preferred_timing is d.PreferredTiming.EVENING
        assert p.gym_personality is d.GymPersonality.CHILL_RELAXED
        assert p.onboarding_completed is False


# ---------------------------------------------------------------------------
# Profile aggregate — Step 2
# ---------------------------------------------------------------------------
class TestProfileCompleteStep2:
    def _started(self):
        return d.Profile.start(
            client_id=1,
            primary_goal=d.PrimaryGoal.IMPROVE_ENDURANCE,
            activity_interests=_interests("Running", "Cardio"),
            preferred_timing=d.PreferredTiming.MORNING,
            gym_personality=d.GymPersonality.COMPETITIVE,
        )

    def test_single_photo_completes_onboarding(self):
        p = self._started()
        p.complete_step2(
            photos=[_photo(1, 0)],
            bio=d.Bio("Looking for a steady morning partner."),
        )
        assert p.onboarding_completed is True
        assert len(p.photos) == 1
        assert p.photos[0].is_primary is True
        assert p.bio.value.startswith("Looking for")

    def test_three_photos_first_is_primary(self):
        p = self._started()
        p.complete_step2(
            photos=[_photo(1, 0), _photo(1, 1), _photo(1, 2)],
            bio=None,
        )
        assert [ph.is_primary for ph in p.photos] == [True, False, False]
        assert p.bio is None

    def test_photos_get_reindexed(self):
        # Caller might send orders out of sequence — should normalise.
        p = self._started()
        p.complete_step2(
            photos=[_photo(1, 0), _photo(1, 1), _photo(1, 2)],
            bio=None,
        )
        assert [ph.display_order for ph in p.photos] == [0, 1, 2]

    def test_zero_photos_completes_onboarding_without_writes(self):
        """Both photos and bio optional — empty call just flips the flag."""
        p = self._started()
        p.complete_step2(photos=[], bio=None)
        assert p.onboarding_completed is True
        assert p.photos == []
        assert p.bio is None

    def test_only_bio_provided_preserves_empty_photos(self):
        p = self._started()
        p.complete_step2(photos=[], bio=d.Bio("Just here for accountability"))
        assert p.onboarding_completed is True
        assert p.photos == []
        assert p.bio.value == "Just here for accountability"

    def test_only_photos_provided_preserves_empty_bio(self):
        p = self._started()
        p.complete_step2(photos=[_photo(1, 0)], bio=None)
        assert p.onboarding_completed is True
        assert len(p.photos) == 1
        assert p.bio is None

    def test_resubmit_without_photos_preserves_existing_photos(self):
        """User finishes onboarding with photos, then resubmits with empty
        photo_paths — existing photos must not be wiped."""
        p = self._started()
        p.complete_step2(
            photos=[_photo(1, 0), _photo(1, 1)],
            bio=d.Bio("first bio"),
        )
        existing_photos = list(p.photos)

        p.complete_step2(photos=[], bio=None)
        assert p.photos == existing_photos          # untouched
        assert p.bio.value == "first bio"           # untouched
        assert p.onboarding_completed is True

    def test_resubmit_with_only_bio_preserves_existing_photos(self):
        p = self._started()
        p.complete_step2(photos=[_photo(1, 0)], bio=d.Bio("old"))
        p.complete_step2(photos=[], bio=d.Bio("new"))
        assert len(p.photos) == 1                   # photos untouched
        assert p.bio.value == "new"                 # bio replaced

    def test_rejects_more_than_three_photos(self):
        p = self._started()
        too_many = [_photo(1, i) for i in range(3)] + [_photo(1, 0)]
        with pytest.raises(d.TooFewPhotos):
            p.complete_step2(photos=too_many, bio=None)

    def test_can_recomplete_step2_for_edits(self):
        p = self._started()
        p.complete_step2(photos=[_photo(1, 0)], bio=d.Bio("old bio"))
        assert p.onboarding_completed is True

        p.complete_step2(
            photos=[_photo(1, 0), _photo(1, 1)],
            bio=d.Bio("new bio"),
        )
        assert len(p.photos) == 2
        assert p.bio.value == "new bio"
