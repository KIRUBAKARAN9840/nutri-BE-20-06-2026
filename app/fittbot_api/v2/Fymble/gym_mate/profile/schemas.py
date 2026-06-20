"""Public DTOs returned by the profile module's API port.

These are the cross-module contract. Changing them is a breaking change
for every other module that calls ProfileAPI. ORM objects never leave
the repository; these are what other modules see.

Response shapes mirror the existing v1 conventions used in
profile_pic.py / gym_profile.py / agreement_acceptance.py — presigned
POST uploads return `{upload: {url, fields, key}, cdn_url, version}`,
and image-bearing reads expose a ready-to-render `cdn_url`.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel

# Direct import from friends.schemas (NOT friends.__init__) to avoid the
# circular path:  profile/__init__ → schemas → friends/__init__ →
# friends/_repository → profile/__init__.
from app.fittbot_api.v2.Fymble.gym_mate.friends.schemas import (
    FriendSuggestionDTO,
    MutualFriendDTO,
    RelationshipDTO,
)


class PhotoDTO(BaseModel):
    s3_path: str        # raw S3 key — for re-uploads / debugging
    cdn_url: str        # full URL the frontend renders directly
    display_order: int
    is_primary: bool


class ProfileSummaryDTO(BaseModel):
    """Lightweight view used for cards, headers, list items.

    `primary_photo_url` is a ready-to-render CDN URL (no construction
    needed on the client).
    """
    client_id: int
    primary_photo_url: Optional[str] = None
    bio: Optional[str] = None
    onboarding_completed: bool


class ProfileMatchAttributesDTO(BaseModel):
    """Inputs the sessions module needs to compute a match score."""
    client_id: int
    primary_goal: str
    activity_interests: List[str]
    preferred_timing: str
    gym_personality: str


class OnboardingStatusDTO(BaseModel):
    """Where in the onboarding flow this client currently is.

    next_step:
        1 — Step 1 not submitted yet
        2 — Step 1 done, Step 2 pending
        0 — Onboarding complete
    """
    client_id: int
    next_step: int
    onboarding_completed: bool


class DefaultAvatarDTO(BaseModel):
    """One option from `gym_mate.default_profile` — surfaced on Step 1
    response so the frontend's Step 2 photo picker can pre-render a
    gallery of gender-matched avatars while the user fills out bio."""
    sno: int
    profile_url: str


class SocialCountsDTO(BaseModel):
    """Counts shown in the profile header."""
    friends_count: int = 0
    pending_received_requests_count: int = 0


class FullProfileDTO(BaseModel):

    client_id: int
    name: Optional[str] = None
    primary_goal: str
    activity_interests: List[str]
    preferred_timing: str
    gym_personality: str
    # Flat list of the same 4 fields above, in chip-row order — added
    # for parity with friend_suggestions[*].details on /home. The four
    # individual fields above remain for edit-form pre-population.
    details: List[str] = []
    bio: Optional[str]
    city: Optional[str] = None
    photos: List[PhotoDTO]
    onboarding_completed: bool
    social: SocialCountsDTO = SocialCountsDTO()
    # Same key, same shape as on `/home` — frontend renders identically.
    friend_suggestions: List[FriendSuggestionDTO] = []
    # Up to 3 mutual friends — populated ONLY when viewing someone
    # else's profile (view=others). Empty list for own profile.
    mutual_friends: List[MutualFriendDTO] = []
    # Viewer↔target relationship state — null on own profile, set on
    # others'. Drives the CTA: Connect / Cancel / Accept / Friends.
    relationship: Optional[RelationshipDTO] = None
    # Two flat keys (NOT nested): compatibility % and the target's
    # primary goal. Both null on own profile.
    match: Optional[int] = None
    goal: Optional[str] = None


# ---------------------------------------------------------------------------
# Presigned upload — matches existing v1 profile_pic / gym_profile shape
# ---------------------------------------------------------------------------
class PresignedUploadEnvelopeDTO(BaseModel):
    """The raw S3 POST envelope — exactly what boto3 returns.

    `fields` includes the S3 object key, signatures, and a base64 policy.
    The bucket name is in the URL and inside the (signed) policy — S3
    enforces it on POST.
    """
    url: str
    fields: dict


class PresignedPhotoUploadDTO(BaseModel):
    """One presigned upload slot.

    Shape mirrors profile_pic.py / gym_profile.py / agreement_acceptance.py:

        {
          "upload":   { "url": "...", "fields": {...} },
          "cdn_url":  "https://{bucket}.s3.{region}.amazonaws.com/{key}?v={ms}",
          "version":  1716892345000
        }
    """
    upload: PresignedUploadEnvelopeDTO
    cdn_url: str
    version: int
