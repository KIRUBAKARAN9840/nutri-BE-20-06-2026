from datetime import datetime
from typing import Dict, List, Optional, Set

import pytest

from app.fittbot_api.v2.Fymble.gym_mate.friends import _domain as d
from app.fittbot_api.v2.Fymble.gym_mate.friends._events import (
    FriendAdded,
    FriendRemoved,
    FriendRequestAccepted,
    FriendRequestCancelled,
    FriendRequestRejected,
    FriendRequestSent,
)
from app.fittbot_api.v2.Fymble.gym_mate.friends._service import FriendsService
from app.utils.logging_utils import FittbotHTTPException


class FakeFriendRepository:
    def __init__(self):
        self.my_profile: Optional[dict] = None
        self.exclude: Set[int] = set()
        self.mutuals: List[dict] = []
        self.matches: List[dict] = []
        self.fallbacks: List[dict] = []
        self.last_exclude_for_match: Optional[Set[int]] = None
        self.last_exclude_for_fallback: Optional[Set[int]] = None

        # Lifecycle state
        self.onboarded: Set[int] = set()
        self.blocked_pairs: Set[tuple] = set()
        self.friendships: Set[tuple] = set()  # canonical (smaller, larger)
        self.requests: Dict[int, d.FriendRequest] = {}
        self.client_info: Dict[int, dict] = {}
        self._next_request_id = 1

    # Suggestion-side hooks
    async def get_my_profile(self, client_id):
        return self.my_profile

    async def get_exclusion_set(self, viewer_id):
        return set(self.exclude) | {viewer_id}

    async def find_mutual_candidates(self, viewer_id, exclude_ids, limit):
        return [m for m in self.mutuals if m["client_id"] not in exclude_ids][:limit]

    async def find_match_candidates(self, viewer_id, my_profile, exclude_ids, limit):
        self.last_exclude_for_match = set(exclude_ids)
        return [m for m in self.matches if m["client_id"] not in exclude_ids][:limit]

    async def find_fallback_candidates(self, viewer_id, exclude_ids, limit):
        self.last_exclude_for_fallback = set(exclude_ids)
        return [f for f in self.fallbacks if f["client_id"] not in exclude_ids][:limit]

    # Lifecycle hooks
    async def is_recipient_onboarded(self, client_id):
        return client_id in self.onboarded

    async def is_blocked_either_way(self, a, b):
        return (a, b) in self.blocked_pairs or (b, a) in self.blocked_pairs

    async def get_bidirectional_block_ids(self, client_id):
        out = set()
        for a, b in self.blocked_pairs:
            if a == client_id:
                out.add(b)
            elif b == client_id:
                out.add(a)
        return out

    async def is_friend(self, a, b):
        return d.canonical_pair(a, b) in self.friendships

    async def get_pending_either_direction(self, a, b):
        for r in self.requests.values():
            if r.status != d.FriendRequestStatus.PENDING:
                continue
            if {r.from_client_id, r.to_client_id} == {a, b}:
                return r
        return None

    async def add_request(self, request):
        request.id = self._next_request_id
        request.created_at = datetime.now()
        self._next_request_id += 1
        self.requests[request.id] = request
        return request

    async def get_request_by_id(self, request_id):
        return self.requests.get(request_id)

    async def update_request_status(self, request_id, status, responded_at):
        r = self.requests.get(request_id)
        if r is not None:
            r.status = d.FriendRequestStatus(status)
            r.responded_at = responded_at

    async def add_friendship(self, a, b):
        self.friendships.add(d.canonical_pair(a, b))

    async def remove_friendship(self, a, b):
        key = d.canonical_pair(a, b)
        if key in self.friendships:
            self.friendships.remove(key)
            return True
        return False

    async def list_incoming_requests(self, viewer_id, limit=50, offset=0):
        out = []
        for r in sorted(self.requests.values(), key=lambda r: r.id, reverse=True):
            if r.to_client_id != viewer_id or r.status != d.FriendRequestStatus.PENDING:
                continue
            info = self.client_info.get(r.from_client_id, {})
            out.append({
                "request_id": r.id,
                "other_client_id": r.from_client_id,
                "other_name": info.get("name"),
                "other_avatar_url": info.get("avatar_url"),
                "other_primary_goal": info.get("primary_goal"),
                "created_at": r.created_at,
            })
        return out[offset:offset + limit]

    async def list_outgoing_requests(self, viewer_id, limit=50, offset=0):
        out = []
        for r in sorted(self.requests.values(), key=lambda r: r.id, reverse=True):
            if r.from_client_id != viewer_id or r.status != d.FriendRequestStatus.PENDING:
                continue
            info = self.client_info.get(r.to_client_id, {})
            out.append({
                "request_id": r.id,
                "other_client_id": r.to_client_id,
                "other_name": info.get("name"),
                "other_avatar_url": info.get("avatar_url"),
                "other_primary_goal": info.get("primary_goal"),
                "created_at": r.created_at,
            })
        return out[offset:offset + limit]

    async def list_friends(self, viewer_id, limit=100, offset=0):
        out = []
        for (a, b) in self.friendships:
            if a != viewer_id and b != viewer_id:
                continue
            other = b if a == viewer_id else a
            info = self.client_info.get(other, {})
            out.append({
                "client_id": other,
                "name": info.get("name"),
                "avatar_url": info.get("avatar_url"),
                "primary_goal": info.get("primary_goal"),
                "friended_at": None,
            })
        return out[offset:offset + limit]


def _row(cid, name=None, goal=None, mutual=None, pct=None, details=None):
    if details is None:
        details = [goal] if goal else []
    out = {"client_id": cid, "name": name or f"u{cid}",
           "avatar_url": f"https://x/{cid}.jpg", "details": details}
    if mutual is not None:
        out["mutual_count"] = mutual
    if pct is not None:
        out["match_percentage"] = pct
    return out


@pytest.fixture
def repo():
    return FakeFriendRepository()


@pytest.fixture
def service(repo):
    return FriendsService(repository=repo)


class TestWaterfall:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_data(self, service):
        result = await service.suggest_for_home(client_id=42)
        assert result == []

    @pytest.mark.asyncio
    async def test_tier_priority_mutual_first(self, service, repo):
        repo.my_profile = {"gym_id": 1, "primary_goal": "X", "preferred_timing": "Y",
                           "gym_personality": "Z", "activity_interests": []}
        repo.mutuals = [_row(99, mutual=3), _row(88, mutual=2)]
        repo.matches = [_row(77, pct=80)]
        repo.fallbacks = [_row(66)]

        result = await service.suggest_for_home(client_id=42, limit=5)
        types = [(r.client_id, r.suggestion_type) for r in result]
        # Mutuals lead, then match, then fallback
        assert types == [(99, "mutual"), (88, "mutual"), (77, "match"), (66, "fallback")]

    @pytest.mark.asyncio
    async def test_sno_is_1_indexed(self, service, repo):
        repo.mutuals = [_row(99, mutual=1)]
        repo.fallbacks = [_row(88), _row(77), _row(66), _row(55)]
        result = await service.suggest_for_home(client_id=42, limit=5)
        assert [r.sno for r in result] == [1, 2, 3, 4, 5]

    @pytest.mark.asyncio
    async def test_mutual_dto_carries_mutual_count_only(self, service, repo):
        repo.mutuals = [_row(99, mutual=4)]
        result = await service.suggest_for_home(client_id=42, limit=5)
        assert result[0].suggestion_type == "mutual"
        assert result[0].mutual_count == 4
        assert result[0].match_percentage is None

    @pytest.mark.asyncio
    async def test_match_dto_carries_match_percentage_only(self, service, repo):
        repo.my_profile = {"gym_id": 1, "primary_goal": "X", "preferred_timing": "Y",
                           "gym_personality": "Z", "activity_interests": []}
        repo.matches = [_row(99, pct=72)]
        result = await service.suggest_for_home(client_id=42, limit=5)
        assert result[0].suggestion_type == "match"
        assert result[0].match_percentage == 72
        assert result[0].mutual_count is None

    @pytest.mark.asyncio
    async def test_fallback_dto_has_no_extras(self, service, repo):
        repo.fallbacks = [_row(99)]
        result = await service.suggest_for_home(client_id=42, limit=5)
        assert result[0].suggestion_type == "fallback"
        assert result[0].mutual_count is None
        assert result[0].match_percentage is None

    @pytest.mark.asyncio
    async def test_dedupes_across_tiers(self, service, repo):
        # Same client appears in mutual + match — show once with stronger tier
        repo.my_profile = {"gym_id": 1, "primary_goal": "X", "preferred_timing": "Y",
                           "gym_personality": "Z", "activity_interests": []}
        repo.mutuals = [_row(99, mutual=2)]
        repo.matches = [_row(99, pct=85)]   # same client as above
        repo.fallbacks = []
        result = await service.suggest_for_home(client_id=42, limit=5)
        assert len(result) == 1
        assert result[0].suggestion_type == "mutual"

    @pytest.mark.asyncio
    async def test_skips_match_tier_when_no_profile(self, service, repo):
        repo.my_profile = None
        repo.mutuals = []
        repo.matches = [_row(99, pct=99)]   # should be ignored
        repo.fallbacks = [_row(88)]
        result = await service.suggest_for_home(client_id=42, limit=5)
        # Match tier skipped because viewer has no profile
        assert [r.client_id for r in result] == [88]
        assert result[0].suggestion_type == "fallback"

    @pytest.mark.asyncio
    async def test_stops_at_limit(self, service, repo):
        repo.mutuals = [_row(i, mutual=1) for i in range(10)]
        result = await service.suggest_for_home(client_id=42, limit=5)
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_exclusion_grows_across_tiers(self, service, repo):
        # The mutual tier picks 99; subsequent match/fallback queries
        # must see 99 in their exclude set.
        repo.exclude = {500}
        repo.mutuals = [_row(99, mutual=1)]
        repo.my_profile = {"gym_id": 1, "primary_goal": "X", "preferred_timing": "Y",
                           "gym_personality": "Z", "activity_interests": []}
        repo.matches = []
        repo.fallbacks = []
        await service.suggest_for_home(client_id=42, limit=5)
        # Match tier sees exclude + seen
        assert 99 in repo.last_exclude_for_match
        assert 42 in repo.last_exclude_for_match   # self
        assert 500 in repo.last_exclude_for_match  # original exclude
        # Fallback tier likewise
        assert 99 in repo.last_exclude_for_fallback

    @pytest.mark.asyncio
    async def test_partial_fill_returns_what_we_have(self, service, repo):
        # Only mutual + 1 fallback exist
        repo.mutuals = [_row(99, mutual=1)]
        repo.fallbacks = [_row(88)]
        result = await service.suggest_for_home(client_id=42, limit=5)
        assert len(result) == 2
        assert [r.client_id for r in result] == [99, 88]

    @pytest.mark.asyncio
    async def test_extra_exclude_filters_out_target(self, service, repo):
        """Viewing someone's profile should hide that person from the
        suggestion list on the same screen."""
        repo.mutuals = [_row(99, mutual=3), _row(88, mutual=2)]
        result = await service.suggest_for_home(
            client_id=42, limit=5, extra_exclude={99},
        )
        ids = [r.client_id for r in result]
        assert 99 not in ids
        assert 88 in ids


class _FakeRotation:
    """In-memory rotation cache for unit tests."""
    def __init__(self):
        self.shown: set = set()
        self.records: list = []

    async def get_recently_shown(self, client_id, window_seconds=3600):
        return set(self.shown)

    async def record_shown(self, client_id, ids):
        self.records.append(list(ids))
        for i in ids:
            self.shown.add(int(i))


class TestRotation:
    @pytest.mark.asyncio
    async def test_second_call_rotates_out_first_batch(self, repo):
        from app.fittbot_api.v2.Fymble.gym_mate.friends._service import FriendsService
        rot = _FakeRotation()
        svc = FriendsService(repository=repo, rotation=rot)

        # Pool of 10 candidates so the second call has 5 fresh after
        # the first 5 are excluded by rotation.
        repo.mutuals = [_row(c, mutual=2) for c in range(100, 110)]

        first = await svc.suggest_for_home(client_id=42, limit=5)
        first_ids = [r.client_id for r in first]
        assert len(first_ids) == 5

        second = await svc.suggest_for_home(client_id=42, limit=5)
        second_ids = [r.client_id for r in second]
        # All 5 in the second batch should be fresh (none repeat).
        assert set(first_ids).isdisjoint(set(second_ids))

    @pytest.mark.asyncio
    async def test_relaxes_when_pool_smaller_than_limit(self, repo):
        from app.fittbot_api.v2.Fymble.gym_mate.friends._service import FriendsService
        rot = _FakeRotation()
        svc = FriendsService(repository=repo, rotation=rot)

        # Only 3 candidates total
        repo.mutuals = [_row(c, mutual=1) for c in (100, 101, 102)]

        # First call: 3 returned, all recorded
        first = await svc.suggest_for_home(client_id=42, limit=5)
        assert len(first) == 3

        # Second call: rotation would exclude all 3 → relax kicks in and
        # we still get up to 3 (rather than 0)
        second = await svc.suggest_for_home(client_id=42, limit=5)
        assert len(second) == 3

    @pytest.mark.asyncio
    async def test_no_rotation_when_redis_missing(self, repo):
        """Service without rotation should behave identically to v1."""
        from app.fittbot_api.v2.Fymble.gym_mate.friends._service import FriendsService
        svc = FriendsService(repository=repo, rotation=None)
        repo.mutuals = [_row(c, mutual=1) for c in (100, 101, 102, 103, 104)]
        first = await svc.suggest_for_home(client_id=42, limit=5)
        second = await svc.suggest_for_home(client_id=42, limit=5)
        # Identical because nothing was tracked
        assert [r.client_id for r in first] == [r.client_id for r in second]


# ─── Lifecycle tests ────────────────────────────────────────────────


class RecordingBus:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


@pytest.fixture
def bus():
    return RecordingBus()


@pytest.fixture
def lifecycle_service(repo, bus):
    repo.onboarded.update({99, 88, 77, 42, 200})
    return FriendsService(repository=repo, event_bus=bus)


class TestSendRequest:
    @pytest.mark.asyncio
    async def test_creates_pending(self, lifecycle_service, repo, bus):
        result = await lifecycle_service.send_request(
            from_client_id=42, to_client_id=99,
        )
        assert result.status == "pending"
        assert result.from_client_id == 42
        assert result.to_client_id == 99
        assert any(isinstance(e, FriendRequestSent) for e in bus.events)

    @pytest.mark.asyncio
    async def test_self_rejected(self, lifecycle_service):
        with pytest.raises(FittbotHTTPException) as exc:
            await lifecycle_service.send_request(
                from_client_id=42, to_client_id=42,
            )
        assert exc.value.error_code == "GYMMATE_FR_INVALID"

    @pytest.mark.asyncio
    async def test_recipient_not_onboarded(self, lifecycle_service, repo):
        repo.onboarded.discard(99)
        with pytest.raises(FittbotHTTPException) as exc:
            await lifecycle_service.send_request(
                from_client_id=42, to_client_id=99,
            )
        assert exc.value.error_code == "GYMMATE_FR_INVALID"

    @pytest.mark.asyncio
    async def test_blocked_rejected(self, lifecycle_service, repo):
        repo.blocked_pairs.add((42, 99))
        with pytest.raises(FittbotHTTPException) as exc:
            await lifecycle_service.send_request(
                from_client_id=42, to_client_id=99,
            )
        assert exc.value.error_code == "GYMMATE_FR_BLOCKED"

    @pytest.mark.asyncio
    async def test_already_friends_rejected(self, lifecycle_service, repo):
        repo.friendships.add(d.canonical_pair(42, 99))
        with pytest.raises(FittbotHTTPException) as exc:
            await lifecycle_service.send_request(
                from_client_id=42, to_client_id=99,
            )
        assert exc.value.error_code == "GYMMATE_FR_ALREADY_FRIENDS"

    @pytest.mark.asyncio
    async def test_duplicate_pending_is_idempotent(self, lifecycle_service, bus):
        first = await lifecycle_service.send_request(
            from_client_id=42, to_client_id=99,
        )
        bus.events.clear()
        second = await lifecycle_service.send_request(
            from_client_id=42, to_client_id=99,
        )
        assert first.request_id == second.request_id
        assert bus.events == []

    @pytest.mark.asyncio
    async def test_reverse_pending_is_idempotent(self, lifecycle_service, bus):
        # If 99 already sent to 42, 42 re-sending to 99 returns the existing.
        first = await lifecycle_service.send_request(
            from_client_id=99, to_client_id=42,
        )
        bus.events.clear()
        second = await lifecycle_service.send_request(
            from_client_id=42, to_client_id=99,
        )
        assert first.request_id == second.request_id


class TestAcceptReject:
    @pytest.mark.asyncio
    async def test_accept_creates_friendship(self, lifecycle_service, repo, bus):
        r = await lifecycle_service.send_request(42, 99)
        bus.events.clear()

        await lifecycle_service.accept_request(recipient_id=99, request_id=r.request_id)
        assert d.canonical_pair(42, 99) in repo.friendships
        assert any(isinstance(e, FriendRequestAccepted) for e in bus.events)
        assert any(isinstance(e, FriendAdded) for e in bus.events)

    @pytest.mark.asyncio
    async def test_only_recipient_can_accept(self, lifecycle_service):
        r = await lifecycle_service.send_request(42, 99)
        with pytest.raises(FittbotHTTPException) as exc:
            await lifecycle_service.accept_request(recipient_id=42, request_id=r.request_id)
        assert exc.value.error_code == "GYMMATE_FR_FORBIDDEN"

    @pytest.mark.asyncio
    async def test_double_accept_rejected(self, lifecycle_service):
        r = await lifecycle_service.send_request(42, 99)
        await lifecycle_service.accept_request(recipient_id=99, request_id=r.request_id)
        with pytest.raises(FittbotHTTPException) as exc:
            await lifecycle_service.accept_request(recipient_id=99, request_id=r.request_id)
        assert exc.value.error_code == "GYMMATE_FR_BAD_STATE"

    @pytest.mark.asyncio
    async def test_reject(self, lifecycle_service, repo, bus):
        r = await lifecycle_service.send_request(42, 99)
        bus.events.clear()
        await lifecycle_service.reject_request(recipient_id=99, request_id=r.request_id)
        assert d.canonical_pair(42, 99) not in repo.friendships
        assert any(isinstance(e, FriendRequestRejected) for e in bus.events)

    @pytest.mark.asyncio
    async def test_request_not_found(self, lifecycle_service):
        with pytest.raises(FittbotHTTPException) as exc:
            await lifecycle_service.accept_request(recipient_id=99, request_id=9999)
        assert exc.value.error_code == "GYMMATE_FR_NOT_FOUND"


class TestCancel:
    @pytest.mark.asyncio
    async def test_sender_can_cancel(self, lifecycle_service, bus):
        r = await lifecycle_service.send_request(42, 99)
        bus.events.clear()
        await lifecycle_service.cancel_request(sender_id=42, request_id=r.request_id)
        assert any(isinstance(e, FriendRequestCancelled) for e in bus.events)

    @pytest.mark.asyncio
    async def test_only_sender_can_cancel(self, lifecycle_service):
        r = await lifecycle_service.send_request(42, 99)
        with pytest.raises(FittbotHTTPException) as exc:
            await lifecycle_service.cancel_request(sender_id=99, request_id=r.request_id)
        assert exc.value.error_code == "GYMMATE_FR_FORBIDDEN"


class TestListings:
    @pytest.mark.asyncio
    async def test_incoming_only_pending_to_me(self, lifecycle_service, repo):
        repo.client_info[42] = {"name": "Me", "avatar_url": "u/42"}
        await lifecycle_service.send_request(42, 99)
        await lifecycle_service.send_request(88, 99)
        rows = await lifecycle_service.list_incoming(client_id=99)
        senders = {r.other_client_id for r in rows}
        assert senders == {42, 88}

    @pytest.mark.asyncio
    async def test_outgoing_only_pending_from_me(self, lifecycle_service):
        await lifecycle_service.send_request(42, 99)
        await lifecycle_service.send_request(42, 88)
        rows = await lifecycle_service.list_outgoing(client_id=42)
        recipients = {r.other_client_id for r in rows}
        assert recipients == {99, 88}

    @pytest.mark.asyncio
    async def test_friends_list_after_accept(self, lifecycle_service, repo):
        repo.client_info[99] = {"name": "Anamika", "avatar_url": "u/99", "primary_goal": "Stay Fit"}
        r = await lifecycle_service.send_request(42, 99)
        await lifecycle_service.accept_request(recipient_id=99, request_id=r.request_id)
        friends = await lifecycle_service.list_friends(client_id=42)
        assert len(friends) == 1
        assert friends[0].client_id == 99


class TestUnfriend:
    @pytest.mark.asyncio
    async def test_unfriend_removes_pair(self, lifecycle_service, repo, bus):
        repo.friendships.add(d.canonical_pair(42, 99))
        await lifecycle_service.unfriend(client_id=42, other_id=99)
        assert d.canonical_pair(42, 99) not in repo.friendships
        assert any(isinstance(e, FriendRemoved) for e in bus.events)

    @pytest.mark.asyncio
    async def test_unfriend_not_friends_404(self, lifecycle_service):
        with pytest.raises(FittbotHTTPException) as exc:
            await lifecycle_service.unfriend(client_id=42, other_id=99)
        assert exc.value.error_code == "GYMMATE_FR_NOT_FOUND"


class TestOnChangeCallback:
    @pytest.mark.asyncio
    async def test_send_fires_for_both_parties(self, repo):
        calls = []

        async def cb(client_id):
            calls.append(client_id)

        repo.onboarded.update({42, 99})
        svc = FriendsService(repository=repo, on_change=cb)
        await svc.send_request(from_client_id=42, to_client_id=99)
        assert set(calls) == {42, 99}

    @pytest.mark.asyncio
    async def test_accept_fires_for_both(self, repo):
        calls = []

        async def cb(client_id):
            calls.append(client_id)

        repo.onboarded.update({42, 99})
        svc = FriendsService(repository=repo, on_change=cb)
        r = await svc.send_request(42, 99)
        calls.clear()
        await svc.accept_request(recipient_id=99, request_id=r.request_id)
        assert set(calls) == {42, 99}
