"""Pure domain logic for the GymMate profile module.

Nothing in this file does I/O. Everything here is unit-testable with zero
fixtures — no DB, no Redis, no HTTP. Value objects enforce invariants at
construction; the Profile aggregate enforces lifecycle rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional



def _normalize_choice(value: Optional[str]) -> str:
    return (value or "").strip()


class PrimaryGoal(str, Enum):
    WEIGHT_LOSS = "Weight Loss"
    WEIGHT_GAIN = "Weight Gain"
    MUSCLE_BUILDING = "Muscle Building"
    STAY_FIT = "Stay Fit"
    IMPROVE_ENDURANCE = "Improve Endurance"
    FLEXIBILITY_MOBILITY = "Flexibility & Mobility"
    ATHLETIC_PERFORMANCE = "Athletic Performance"
    STRESS_RELIEF = "Stress Relief"

    @classmethod
    def from_input(cls, value: str) -> "PrimaryGoal":
        try:
            return cls(_normalize_choice(value))
        except ValueError:
            raise InvalidChoice(
                f"primary_goal must be one of {[m.value for m in cls]}, "
                f"got {value!r}"
            )


class PreferredTiming(str, Enum):
    MORNING = "Morning"
    EVENING = "Evening"
    FLEXIBLE = "Flexible"

    @classmethod
    def from_input(cls, value: str) -> "PreferredTiming":
        try:
            return cls(_normalize_choice(value))
        except ValueError:
            raise InvalidChoice(
                f"preferred_timing must be one of {[m.value for m in cls]}, "
                f"got {value!r}"
            )


class GymPersonality(str, Enum):
    SERIOUS_FOCUSED = "Serious & Focused"
    FRIENDLY_SOCIAL = "Friendly & Social"
    MOTIVATOR = "Motivator"
    CHILL_RELAXED = "Chill & Relaxed"
    COMPETITIVE = "Competitive"
    BEGINNER_FRIENDLY = "Beginner-friendly"
    NO_NONSENSE = "No-nonsense"

    @classmethod
    def from_input(cls, value: str) -> "GymPersonality":
        try:
            return cls(_normalize_choice(value))
        except ValueError:
            raise InvalidChoice(
                f"gym_personality must be one of {[m.value for m in cls]}, "
                f"got {value!r}"
            )


# Multi-select tags from Activity Interests dropdown. Source of truth:
# the ACTIVITY_OPTIONS list in Frontend gymmate.jsx — keep these in sync.
# "Weightlifting" (no space) is kept for backward compat with old rows.
ACTIVITY_INTEREST_VALUES = frozenset({
    # Current frontend (gymmate.jsx)
    "Gymming", "Body Building", "Weight Lifting",
    "Cardio", "Yoga", "CrossFit",
    "Cycling", "Running", "Swimming", "Martial Arts",
    "Pilates", "HIIT", "Calisthenics", "Zumba",
    # Legacy values still in some DB rows — accepted on read, never
    # produced fresh now that the frontend dropped them.
    "Weightlifting",
})



@dataclass(frozen=True)
class Bio:
    """Trimmed, length-bounded bio text from Step 2 of onboarding."""
    value: str

    MAX_LEN = 300

    def __post_init__(self) -> None:
        trimmed = self.value.strip()
        if len(trimmed) > self.MAX_LEN:
            raise InvalidBio(f"bio max {self.MAX_LEN} chars, got {len(trimmed)}")
        # frozen dataclasses can't normally mutate, but __post_init__ can use __setattr__
        object.__setattr__(self, "value", trimmed)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class ActivityInterests:
    """Set of selected activity tags. Validates each value is recognised."""
    values: tuple[str, ...]

    MIN_COUNT = 1

    def __post_init__(self) -> None:
        # Normalise each tag (strip whitespace) before validating against
        # the whitelist. Frontend picker rows can carry surrounding
        # spaces that would otherwise hard-fail.
        normalised = tuple(_normalize_choice(v) for v in self.values)
        deduped = tuple(dict.fromkeys(v for v in normalised if v))
        if len(deduped) < self.MIN_COUNT:
            raise InvalidActivityInterests(f"select at least {self.MIN_COUNT} activity")
        unknown = [v for v in deduped if v not in ACTIVITY_INTEREST_VALUES]
        if unknown:
            raise InvalidActivityInterests(f"unknown activities: {unknown}")
        object.__setattr__(self, "values", deduped)

    def as_list(self) -> list[str]:
        return list(self.values)


@dataclass(frozen=True)
class PhotoSlot:

    s3_path: str
    display_order: int

    MAX_ORDER = 2
    MAX_KEY_LEN = 500
    REQUIRED_KEY_PREFIX = "gym_mate/profile/"

    def __post_init__(self) -> None:
        if not self.s3_path:
            raise InvalidPhoto("photo key is empty")
        if len(self.s3_path) > self.MAX_KEY_LEN:
            raise InvalidPhoto(f"photo key exceeds {self.MAX_KEY_LEN} chars")
 
        is_s3_key = self.s3_path.startswith(self.REQUIRED_KEY_PREFIX)
        is_http_url = self.s3_path.startswith(("http://", "https://"))
        if not (is_s3_key or is_http_url):
            raise InvalidPhoto(
                f"photo key must start with '{self.REQUIRED_KEY_PREFIX}' "
                f"or be a full http(s) URL"
            )
        if not 0 <= self.display_order <= self.MAX_ORDER:
            raise InvalidPhoto(f"display_order must be 0..{self.MAX_ORDER}")

    @property
    def is_primary(self) -> bool:
        return self.display_order == 0


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------
class ProfileDomainError(Exception):
    """Base class for any business-rule violation in the profile module."""


class InvalidBio(ProfileDomainError): ...
class InvalidActivityInterests(ProfileDomainError): ...
class InvalidPhoto(ProfileDomainError): ...
class InvalidChoice(ProfileDomainError):
    """Raised when a dropdown value (primary_goal / preferred_timing /
    gym_personality) doesn't match the enum after whitespace
    normalisation. Holds the bad input so the HTTP layer can surface a
    useful 400 message."""
class OnboardingStepOutOfOrder(ProfileDomainError): ...
class TooFewPhotos(ProfileDomainError): ...


# ---------------------------------------------------------------------------
# Profile aggregate
#
# Small aggregate. Invariants:
#   - Step 1 fields (goal, interests, timing, personality) are required
#     before Step 2 can complete.
#   - Step 2 (photos + bio + mark complete) requires at least 1 photo
#     and at most 3.
#   - The first photo (display_order == 0) is always the primary.
#   - onboarding_completed flips to True exactly once when Step 2 finishes.
# ---------------------------------------------------------------------------
@dataclass
class Profile:
    client_id: int

    # Step 1 fields
    primary_goal: PrimaryGoal
    activity_interests: ActivityInterests
    preferred_timing: PreferredTiming
    gym_personality: GymPersonality

    # Step 1 (optional): current city — free text, may be NULL on legacy rows
    city: Optional[str] = None

    # Step 2 fields
    bio: Optional[Bio] = None
    photos: List[PhotoSlot] = field(default_factory=list)

    # Lifecycle
    onboarding_completed: bool = False

    # Persistence id — set by the repository after first save
    id: Optional[int] = None

    # Transient: True when the caller intentionally rewrote `photos`
    # (replace or clear). The repo uses this to decide whether to issue
    # a DELETE on profile_photo. Without it, distinguishing "FE didn't
    # send photo_paths" from "FE explicitly cleared photo_paths" was
    # impossible. Resets to False on every fresh load from DB.
    _photos_dirty: bool = field(default=False, repr=False, compare=False)

    MAX_PHOTOS = 3

    @classmethod
    def start(
        cls,
        client_id: int,
        primary_goal: PrimaryGoal,
        activity_interests: ActivityInterests,
        preferred_timing: PreferredTiming,
        gym_personality: GymPersonality,
        city: Optional[str] = None,
    ) -> "Profile":
        """Factory for Step 1 — marks the profile as onboarding-complete
        on Step 1. Step 2 (photos + bio) still runs as before, but the
        profile already counts as completed once Step 1 is submitted."""
        return cls(
            client_id=client_id,
            primary_goal=primary_goal,
            activity_interests=activity_interests,
            preferred_timing=preferred_timing,
            gym_personality=gym_personality,
            city=city,
            onboarding_completed=True,
        )

    def update_step1(
        self,
        primary_goal: PrimaryGoal,
        activity_interests: ActivityInterests,
        preferred_timing: PreferredTiming,
        gym_personality: GymPersonality,
        city: Optional[str] = None,
    ) -> None:
        """Re-submit Step 1 (user edits before completing onboarding)."""
        self.primary_goal = primary_goal
        self.activity_interests = activity_interests
        self.preferred_timing = preferred_timing
        self.gym_personality = gym_personality
        self.city = city
        # Step 1 alone marks the profile as onboarding-complete.
        self.onboarding_completed = True

    def update_partial(
        self,
        primary_goal: Optional[PrimaryGoal] = None,
        activity_interests: Optional[ActivityInterests] = None,
        preferred_timing: Optional[PreferredTiming] = None,
        gym_personality: Optional[GymPersonality] = None,
        bio: Optional[Bio] = None,
        clear_bio: bool = False,
        city: Optional[str] = None,
        photos: Optional[list[PhotoSlot]] = None,
        clear_photos: bool = False,
    ) -> None:
        """Patch-style update — used by the Edit Profile endpoint.

        Every argument is optional. Only fields explicitly provided change.
        - `bio=None` and `clear_bio=False` → bio untouched.
        - `clear_bio=True`                  → bio set to None.
        - `photos=None`, `clear_photos=False`→ photos untouched.
        - `clear_photos=True`               → wipe all existing photos
                                              (user removed their profile pic).
        - `photos=[...]`                    → replace existing photos.
        """
        if primary_goal is not None:
            self.primary_goal = primary_goal
        if activity_interests is not None:
            self.activity_interests = activity_interests
        if preferred_timing is not None:
            self.preferred_timing = preferred_timing
        if gym_personality is not None:
            self.gym_personality = gym_personality

        if clear_bio:
            self.bio = None
        elif bio is not None:
            self.bio = bio

        # city: None → untouched; "" (or blank) → clear to NULL; text → set.
        if city is not None:
            self.city = city or None

        if clear_photos:
            self.photos = []
            self._photos_dirty = True
        elif photos:
            if len(photos) > self.MAX_PHOTOS:
                raise TooFewPhotos(f"at most {self.MAX_PHOTOS} photos allowed")
            sorted_photos = sorted(photos, key=lambda p: p.display_order)
            self.photos = [
                PhotoSlot(s3_path=p.s3_path, display_order=i)
                for i, p in enumerate(sorted_photos)
            ]
            self._photos_dirty = True

    def complete_step2(
        self,
        photos: list[PhotoSlot],
        bio: Optional[Bio],
    ) -> None:
        """Step 2 — finish onboarding.

        Photos and bio are BOTH optional. Behaviour:
          - photos non-empty  →  replace existing photos with the new set
          - photos empty/None →  existing photos are untouched
          - bio non-None      →  replace existing bio
          - bio None          →  existing bio is untouched
          - Always flips onboarding_completed = True

        Raises if Step 1 wasn't submitted first or if photo count exceeds
        the maximum.
        """
        if not all([self.primary_goal, self.activity_interests,
                    self.preferred_timing, self.gym_personality]):
            raise OnboardingStepOutOfOrder("Step 1 must be submitted before Step 2")

        if photos:
            if len(photos) > self.MAX_PHOTOS:
                raise TooFewPhotos(f"at most {self.MAX_PHOTOS} photos allowed")
            # Normalise display_order to a contiguous 0..N sequence so the
            # first is always primary. Honor the order the client sent.
            sorted_photos = sorted(photos, key=lambda p: p.display_order)
            self.photos = [
                PhotoSlot(s3_path=p.s3_path, display_order=i)
                for i, p in enumerate(sorted_photos)
            ]
            self._photos_dirty = True

        if bio is not None:
            self.bio = bio

        self.onboarding_completed = True


def build_profile_details(
    goal: Optional[str],
    interests: Optional[List[str]],
    timing: Optional[str],
    personality: Optional[str],
) -> List[str]:
    """Flatten the four profile attributes into a chip-row list.

    Order: goal first, then each interest, then timing, then personality.
    Nulls and empty strings are dropped. Shared by:
        - profile.FullProfileDTO.details (own profile view)
        - friends.FriendSuggestionDTO.details (suggestions on home)
    so both surfaces render identically.
    """
    out: List[str] = []
    if goal:
        out.append(goal)
    for item in (interests or []):
        if item:
            out.append(item)
    if timing:
        out.append(timing)
    if personality:
        out.append(personality)
    return out
