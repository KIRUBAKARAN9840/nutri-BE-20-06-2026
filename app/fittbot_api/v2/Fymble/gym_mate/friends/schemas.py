from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class FriendSuggestionDTO(BaseModel):
    sno: int
    client_id: int
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    # Free-text bio from the suggested user's profile.
    bio: Optional[str] = None
    # Flat list of every profile attribute the user filled — goal,
    # each activity interest, preferred timing, and gym personality.
    # The frontend can render each as a chip. Nulls are dropped.
    details: List[str] = []
    suggestion_type: str  # "mutual" | "match" | "fallback"
    mutual_count: Optional[int] = None
    match_percentage: Optional[int] = None


class DiscoverProfileDTO(BaseModel):
    """A card in the swipe-to-connect deck. Same shape as
    FriendSuggestionDTO plus `city`. Ordering of the deck is encoded by
    list position (matches first, then same-city, then recent profiles)
    and labelled per-card via `suggestion_type`."""
    sno: int
    client_id: int
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    details: List[str] = []
    city: Optional[str] = None
    suggestion_type: str  # "match" | "same_city" | "fallback"
    match_percentage: Optional[int] = None


class FriendSuggestionSlimDTO(BaseModel):
    """Slim shape used by the "View all" screen — just name + dp,
    plus mutual_count when type=mutual, match_percentage when type=match."""
    sno: int
    client_id: int
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    mutual_count: Optional[int] = None
    match_percentage: Optional[int] = None


class OnboardingSuggestionDTO(BaseModel):
    """Minimal gym-mate card surfaced on the Step 2 onboarding response
    so a fresh user lands on a populated 'find friends' screen instead
    of an empty one. No mutual_count / match_percentage — Step 2 isn't
    the place to show numbers; FE just renders DP + name."""
    client_id: int
    name: Optional[str] = None
    avatar_url: Optional[str] = None


class FriendRequestDTO(BaseModel):
    request_id: int
    from_client_id: int
    to_client_id: int
    status: str
    created_at: Optional[datetime] = None
    responded_at: Optional[datetime] = None


class IncomingRequestDTO(BaseModel):
    request_id: int
    other_client_id: int
    other_name: Optional[str] = None
    other_avatar_url: Optional[str] = None
    other_primary_goal: Optional[str] = None
    created_at: Optional[datetime] = None


class OutgoingRequestDTO(BaseModel):
    request_id: int
    other_client_id: int
    other_name: Optional[str] = None
    other_avatar_url: Optional[str] = None
    other_primary_goal: Optional[str] = None
    created_at: Optional[datetime] = None


class FriendDTO(BaseModel):
    client_id: int
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    primary_goal: Optional[str] = None
    friended_at: Optional[datetime] = None


class MutualFriendDTO(BaseModel):
    """Slim view used on someone-else's profile — just name + DP."""
    client_id: int
    name: Optional[str] = None
    avatar_url: Optional[str] = None


class RelationshipDTO(BaseModel):
    """Viewer ↔ target relationship state. Drives which CTA the
    frontend shows ("Connect" / "Cancel request" / "Accept" / "Friends")."""
    status: str
    # status values:
    #   "none"             — no friendship, no pending request
    #   "friends"          — already friends
    #   "request_sent"     — viewer sent a request to target (pending)
    #   "request_received" — target sent a request to viewer (pending)
    request_id: Optional[int] = None  # the pending request id (cancel/accept/reject)


from typing import List as _List


class MatchInfoDTO(BaseModel):
    """Two keys only:
        - percentage: 0–100 overall similarity (same weights as
          friend_suggestions[type=match])
        - goals: the target's full set of profile selections (goal,
          interests, timing, personality), in chip-row order
    """
    percentage: int
    goals: _List[str] = []
