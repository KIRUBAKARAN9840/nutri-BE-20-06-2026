"""Unit tests for the GymMate profile ORM models.

These tests verify the model *definition* — column types, defaults,
relationships, enum tuples — without touching a real database. Real
DB-level guarantees (UNIQUE constraint enforcement, FK cascade, ENUM
rejection) are exercised in `tests/integration/gymmate/` against a
disposable MySQL.
"""

import pytest

from app.models.fittbot_models.gymmate import (
    GYM_PERSONALITY_VALUES,
    GYMMATE_SCHEMA,
    PREFERRED_TIMING_VALUES,
    PRIMARY_GOAL_VALUES,
    GymMateProfile,
    GymMateProfilePhoto,
)


# ---------------------------------------------------------------------------
# Enum value sets — guard against accidental mutation
# ---------------------------------------------------------------------------
class TestEnumValueSets:
    def test_primary_goal_values_are_frozen(self):
        assert PRIMARY_GOAL_VALUES == (
            "Weight Loss",
            "Weight Gain",
            "Muscle Building",
            "Stay Fit",
            "Improve Endurance",
            "Flexibility & Mobility",
            "Athletic Performance",
            "Stress Relief",
        )

    def test_preferred_timing_values_are_frozen(self):
        assert PREFERRED_TIMING_VALUES == (
            "Early Morning (5–7 AM)",
            "Morning (7–10 AM)",
            "Afternoon (12–3 PM)",
            "Evening (5–8 PM)",
            "Late Night (8–11 PM)",
            "Flexible",
        )

    def test_gym_personality_values_are_frozen(self):
        assert GYM_PERSONALITY_VALUES == (
            "Serious & Focused",
            "Friendly & Social",
            "Motivator",
            "Chill & Relaxed",
            "Competitive",
            "Beginner-friendly",
            "No-nonsense",
        )

    def test_schema_name_is_gym_mate(self):
        assert GYMMATE_SCHEMA == "gym_mate"

    def test_all_values_fit_in_varchar_30(self):
        for v in PRIMARY_GOAL_VALUES + PREFERRED_TIMING_VALUES + GYM_PERSONALITY_VALUES:
            assert len(v) <= 30, f"{v!r} exceeds VARCHAR(30)"


# ---------------------------------------------------------------------------
# GymMateProfile — table shape
# ---------------------------------------------------------------------------
class TestProfileTable:
    def test_table_name_is_profile(self):
        assert GymMateProfile.__tablename__ == "profile"

    def test_table_is_in_gym_mate_schema(self):
        assert GymMateProfile.__table__.schema == GYMMATE_SCHEMA

    def test_required_columns_exist(self):
        expected = {
            "id",
            "client_id",
            "primary_goal",
            "activity_interests",
            "preferred_timing",
            "gym_personality",
            "bio",
            "onboarding_completed",
            "created_at",
            "updated_at",
        }
        actual = {c.name for c in GymMateProfile.__table__.columns}
        assert expected == actual, f"missing: {expected - actual}, extra: {actual - expected}"

    def test_id_is_primary_key(self):
        pk_cols = [c.name for c in GymMateProfile.__table__.primary_key.columns]
        assert pk_cols == ["id"]

    def test_client_id_is_unique(self):
        uniques = [c for c in GymMateProfile.__table__.constraints
                   if c.__class__.__name__ == "UniqueConstraint"]
        unique_cols = {tuple(col.name for col in u.columns) for u in uniques}
        assert ("client_id",) in unique_cols

    def test_bio_is_nullable_others_not(self):
        cols = {c.name: c for c in GymMateProfile.__table__.columns}
        assert cols["bio"].nullable is True
        assert cols["client_id"].nullable is False
        assert cols["primary_goal"].nullable is False
        assert cols["activity_interests"].nullable is False

    def test_onboarding_completed_defaults_false(self):
        cols = {c.name: c for c in GymMateProfile.__table__.columns}
        assert cols["onboarding_completed"].default.arg is False

    def test_photos_relationship_cascades(self):
        rel = GymMateProfile.__mapper__.relationships["photos"]
        # SQLAlchemy stores cascade as a CascadeOptions object
        assert "delete-orphan" in rel.cascade
        assert "delete" in rel.cascade


# ---------------------------------------------------------------------------
# GymMateProfile — Python-level instantiation
# ---------------------------------------------------------------------------
class TestProfileInstantiation:
    def test_can_construct_minimal_profile(self):
        profile = GymMateProfile(
            client_id=42,
            primary_goal="Muscle Building",
            activity_interests=["Weightlifting", "HIIT"],
            preferred_timing="Morning (7–10 AM)",
            gym_personality="Serious & Focused",
        )
        assert profile.client_id == 42
        assert profile.primary_goal == "Muscle Building"
        assert profile.activity_interests == ["Weightlifting", "HIIT"]
        assert profile.bio is None

    def test_can_construct_with_bio(self):
        profile = GymMateProfile(
            client_id=42,
            primary_goal="Weight Loss",
            activity_interests=["Cardio"],
            preferred_timing="Evening (5–8 PM)",
            gym_personality="Chill & Relaxed",
            bio="Trying to lose 5kg, looking for a steady partner.",
        )
        assert profile.bio.startswith("Trying to")

    def test_can_attach_photos(self):
        profile = GymMateProfile(
            client_id=42,
            primary_goal="Improve Endurance",
            activity_interests=["Running"],
            preferred_timing="Early Morning (5–7 AM)",
            gym_personality="Competitive",
        )
        profile.photos = [
            GymMateProfilePhoto(s3_path="s3://b/p1.jpg", display_order=0, is_primary=True),
            GymMateProfilePhoto(s3_path="s3://b/p2.jpg", display_order=1, is_primary=False),
        ]
        assert len(profile.photos) == 2
        assert profile.photos[0].is_primary is True
        assert profile.photos[1].is_primary is False


# ---------------------------------------------------------------------------
# GymMateProfilePhoto — table shape
# ---------------------------------------------------------------------------
class TestProfilePhotoTable:
    def test_table_name_is_profile_photo(self):
        assert GymMateProfilePhoto.__tablename__ == "profile_photo"

    def test_table_is_in_gym_mate_schema(self):
        assert GymMateProfilePhoto.__table__.schema == GYMMATE_SCHEMA

    def test_required_columns_exist(self):
        expected = {
            "id",
            "profile_id",
            "s3_path",
            "display_order",
            "is_primary",
            "created_at",
        }
        actual = {c.name for c in GymMateProfilePhoto.__table__.columns}
        assert expected == actual

    def test_profile_id_has_foreign_key_to_profile(self):
        col = GymMateProfilePhoto.__table__.c.profile_id
        fks = list(col.foreign_keys)
        assert len(fks) == 1
        target = fks[0].target_fullname
        assert target == f"{GYMMATE_SCHEMA}.profile.id"

    def test_profile_id_foreign_key_cascade_delete(self):
        col = GymMateProfilePhoto.__table__.c.profile_id
        fk = next(iter(col.foreign_keys))
        assert fk.ondelete == "CASCADE"

    def test_unique_constraint_on_profile_and_order(self):
        uniques = [c for c in GymMateProfilePhoto.__table__.constraints
                   if c.__class__.__name__ == "UniqueConstraint"]
        unique_cols = {tuple(col.name for col in u.columns) for u in uniques}
        assert ("profile_id", "display_order") in unique_cols


# ---------------------------------------------------------------------------
# GymMateProfilePhoto — Python-level instantiation
# ---------------------------------------------------------------------------
class TestProfilePhotoInstantiation:
    def test_can_construct_photo(self):
        photo = GymMateProfilePhoto(
            profile_id=1,
            s3_path="s3://bucket/key.jpg",
            display_order=0,
            is_primary=True,
        )
        assert photo.profile_id == 1
        assert photo.s3_path == "s3://bucket/key.jpg"
        assert photo.display_order == 0
        assert photo.is_primary is True
