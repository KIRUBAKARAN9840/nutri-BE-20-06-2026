from typing import Awaitable, Callable, List, Optional

from app.utils.logging_utils import FittbotHTTPException

from . import _domain as d
from . import schemas as dto
from ._events import (
    EventBus,
    FriendAdded,
    FriendRemoved,
    FriendRequestAccepted,
    FriendRequestCancelled,
    FriendRequestRejected,
    FriendRequestSent,
    NoopEventBus,
)
from ._repository import FriendRepository
from ._rotation import RotationCache


def _avatar_url_or_none(value: Optional[str]) -> Optional[str]:
    """Pipe stored s3_path / clients.profile through the CDN URL builder.
    Pre-existing http(s) URLs (dummy DPs from seed data) pass through."""
    if not value:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        return value
    from app.fittbot_api.v2.Fymble.gym_mate.profile._storage import build_cdn_url
    return build_cdn_url(value)


OnChange = Optional[Callable[[int], Awaitable[None]]]


class FriendsService:
    """Friend module: suggestions + request lifecycle + friendship CRUD.

    Three-tier waterfall for suggestions:
        1) mutual friends (friends-of-friends, by mutual count)
        2) profile match (similarity score >= MATCH_THRESHOLD)
        3) fallback (most-recently-onboarded onboarded users)
    """

    def __init__(
        self,
        repository: FriendRepository,
        event_bus: Optional[EventBus] = None,
        on_change: OnChange = None,
        rotation: Optional[RotationCache] = None,
    ):
        self.repo = repository
        self.bus = event_bus or NoopEventBus()
        self._on_change = on_change
        # If no rotation cache is wired (e.g. unit tests), suggestions
        # behave exactly as before — no rotation, no Redis writes.
        self.rotation = rotation

    async def _fire(self, *client_ids: int) -> None:
        if self._on_change is None:
            return
        for cid in {c for c in client_ids if c is not None}:
            await self._on_change(cid)

    # ── Suggestions (existing) ────────────────────────────────

    async def suggest_for_home(
        self,
        client_id: int,
        limit: int = 5,
        extra_exclude: Optional[set] = None,
    ) -> List[dto.FriendSuggestionDTO]:
        """3-tier waterfall (mutual → match → fallback) with rotation.

        Rotation: anyone we showed this client within the last hour is
        soft-excluded so refresh feels fresh. If the soft-exclude pool
        empties below `limit`, we relax to HARD exclusions only (still
        skipping friends / pending / blocks) — better to repeat a face
        than show an empty list.
        """
        hard_exclude = await self.repo.get_exclusion_set(client_id)
        if extra_exclude:
            hard_exclude = hard_exclude | set(extra_exclude)

        recently_shown: set = set()
        if self.rotation is not None:
            recently_shown = await self.rotation.get_recently_shown(client_id)

        # First pass: try with rotation (soft) on top of hard excludes.
        results = await self._waterfall(
            client_id, limit, hard_exclude | recently_shown,
        )

        # Relax pass: not enough fresh faces — allow recently-shown back
        # but still respect hard exclusions and the already-picked set.
        if len(results) < limit and recently_shown:
            already_picked = {r["client_id"] for r in results}
            remaining = limit - len(results)
            extra = await self._waterfall(
                client_id, remaining, hard_exclude | already_picked,
            )
            for e in extra:
                if e["client_id"] in already_picked:
                    continue
                results.append(e)
                already_picked.add(e["client_id"])
                if len(results) >= limit:
                    break

        # Record what we showed so the next call rotates them out.
        if self.rotation is not None and results:
            await self.rotation.record_shown(
                client_id, [r["client_id"] for r in results],
            )

        return self._to_suggestion_dtos(results)

    async def _waterfall(
        self,
        client_id: int,
        limit: int,
        exclude: set,
    ) -> List[dict]:
        """One tier-1→2→3 pass with the given exclusion set."""
        results: List[dict] = []
        seen: set = set()

        mutuals = await self.repo.find_mutual_candidates(
            viewer_id=client_id, exclude_ids=exclude, limit=limit,
        )
        for m in mutuals:
            if m["client_id"] in seen:
                continue
            seen.add(m["client_id"])
            m["suggestion_type"] = "mutual"
            results.append(m)
            if len(results) >= limit:
                return results

        my_profile = await self.repo.get_my_profile(client_id)
        if my_profile is not None:
            matches = await self.repo.find_match_candidates(
                viewer_id=client_id,
                my_profile=my_profile,
                exclude_ids=exclude | seen,
                limit=limit - len(results),
            )
            for m in matches:
                if m["client_id"] in seen:
                    continue
                seen.add(m["client_id"])
                m["suggestion_type"] = "match"
                results.append(m)
                if len(results) >= limit:
                    return results

        remaining = limit - len(results)
        if remaining > 0:
            fallback = await self.repo.find_fallback_candidates(
                viewer_id=client_id,
                exclude_ids=exclude | seen,
                limit=remaining,
            )
            for f in fallback:
                if f["client_id"] in seen:
                    continue
                seen.add(f["client_id"])
                f["suggestion_type"] = "fallback"
                results.append(f)
                if len(results) >= limit:
                    break

        return results

    # ── Discover deck (swipe-to-connect) ──────────────────────

    async def discover_profiles(
        self,
        client_id: int,
        limit: int = 50,
    ) -> List[dto.DiscoverProfileDTO]:
        """Swipe-to-connect deck.

        Ordering rule (what the product wants): profiles WITH a gym_mate
        photo come first — ALWAYS, regardless of match score. A 0%-match
        user who has a picture outranks a high-match user with no picture.
        Match % is computed only to LABEL the cards; it never changes the
        order.

        Source pool is every onboarded user (excluding self, existing
        friends, pending requests either way, and blocks), already ordered
        photo-first by the repository. Returns up to `limit`.
        """
        exclude = await self.repo.get_exclusion_set(client_id)

        # Photo-first pool of onboarded users (repo orders: has-photo
        # first, then newest). Capped to `limit`, so when there are more
        # than `limit` profiles with a photo we still fill the deck with
        # photo profiles only.
        pool = await self.repo.find_recent_candidates(
            viewer_id=client_id,
            exclude_ids=exclude,
            pool_size=limit,
        )

        # Overlay match % for display only — does NOT affect ordering.
        my_profile = await self.repo.get_my_profile(client_id)
        match_pct: dict = {}
        if my_profile is not None:
            matches = await self.repo.find_match_candidates(
                viewer_id=client_id,
                my_profile=my_profile,
                exclude_ids=exclude,
                limit=limit,
            )
            match_pct = {
                m["client_id"]: m.get("match_percentage") for m in matches
            }
        for r in pool:
            pct = match_pct.get(r["client_id"])
            r["match_percentage"] = pct
            r["suggestion_type"] = "match" if pct is not None else "fallback"

        # Hard guarantee photo-first even if the pool ordering ever
        # changes. Stable sort keeps recency order within each group.
        pool.sort(key=lambda r: r.get("avatar_url") is None)

        return self._to_discover_dtos(pool[:limit])

    # ── Friend-request lifecycle ──────────────────────────────

    async def send_request(
        self, from_client_id: int, to_client_id: int,
    ) -> dto.FriendRequestDTO:
        try:
            request = d.FriendRequest.create(from_client_id, to_client_id)
        except d.CannotFriendSelf as exc:
            raise FittbotHTTPException(
                status_code=400, detail=str(exc),
                error_code="GYMMATE_FR_INVALID",
                log_data={"client_id": from_client_id, "to": to_client_id},
            )

        # Recipient must be onboarded
        if not await self.repo.is_recipient_onboarded(to_client_id):
            raise FittbotHTTPException(
                status_code=400,
                detail="Recipient has not completed gym_mate onboarding",
                error_code="GYMMATE_FR_INVALID",
                log_data={"client_id": from_client_id, "to": to_client_id},
            )
        # Block check
        if await self.repo.is_blocked_either_way(from_client_id, to_client_id):
            raise FittbotHTTPException(
                status_code=403,
                detail="Cannot send a request to a blocked user",
                error_code="GYMMATE_FR_BLOCKED",
                log_data={"client_id": from_client_id, "to": to_client_id},
            )
        # Already friends?
        if await self.repo.is_friend(from_client_id, to_client_id):
            raise FittbotHTTPException(
                status_code=400,
                detail="You are already friends",
                error_code="GYMMATE_FR_ALREADY_FRIENDS",
                log_data={"client_id": from_client_id, "to": to_client_id},
            )
        # Existing pending request either direction → idempotent
        existing = await self.repo.get_pending_either_direction(
            from_client_id, to_client_id,
        )
        if existing is not None:
            return self._to_request_dto(existing)

        saved = await self.repo.add_request(request)
        await self.bus.publish(FriendRequestSent(
            request_id=saved.id,
            from_client_id=from_client_id,
            to_client_id=to_client_id,
        ))
        await self._fire(from_client_id, to_client_id)
        return self._to_request_dto(saved)

    async def accept_request(self, recipient_id: int, request_id: int) -> None:
        request = await self.repo.get_request_by_id(request_id)
        if request is None:
            raise FittbotHTTPException(
                status_code=404, detail="Request not found",
                error_code="GYMMATE_FR_NOT_FOUND",
                log_data={"client_id": recipient_id, "request_id": request_id},
            )
        try:
            request.accept(recipient_id)
        except d.NotRecipient as exc:
            raise FittbotHTTPException(
                status_code=403, detail=str(exc),
                error_code="GYMMATE_FR_FORBIDDEN",
                log_data={"client_id": recipient_id, "request_id": request_id},
            )
        except d.RequestNotPending as exc:
            raise FittbotHTTPException(
                status_code=400, detail=str(exc),
                error_code="GYMMATE_FR_BAD_STATE",
                log_data={"client_id": recipient_id, "request_id": request_id},
            )

        await self.repo.update_request_status(
            request_id, request.status.value, request.responded_at,
        )
        await self.repo.add_friendship(request.from_client_id, request.to_client_id)
        await self.bus.publish(FriendRequestAccepted(
            request_id=request_id,
            from_client_id=request.from_client_id,
            to_client_id=request.to_client_id,
        ))
        await self.bus.publish(FriendAdded(
            client_a_id=min(request.from_client_id, request.to_client_id),
            client_b_id=max(request.from_client_id, request.to_client_id),
        ))
        await self._fire(request.from_client_id, request.to_client_id)

    async def reject_request(self, recipient_id: int, request_id: int) -> None:
        request = await self.repo.get_request_by_id(request_id)
        if request is None:
            raise FittbotHTTPException(
                status_code=404, detail="Request not found",
                error_code="GYMMATE_FR_NOT_FOUND",
                log_data={"client_id": recipient_id, "request_id": request_id},
            )
        try:
            request.reject(recipient_id)
        except d.NotRecipient as exc:
            raise FittbotHTTPException(
                status_code=403, detail=str(exc),
                error_code="GYMMATE_FR_FORBIDDEN",
                log_data={"client_id": recipient_id, "request_id": request_id},
            )
        except d.RequestNotPending as exc:
            raise FittbotHTTPException(
                status_code=400, detail=str(exc),
                error_code="GYMMATE_FR_BAD_STATE",
                log_data={"client_id": recipient_id, "request_id": request_id},
            )

        await self.repo.update_request_status(
            request_id, request.status.value, request.responded_at,
        )
        await self.bus.publish(FriendRequestRejected(
            request_id=request_id,
            from_client_id=request.from_client_id,
            to_client_id=request.to_client_id,
        ))
        await self._fire(request.from_client_id, request.to_client_id)

    async def cancel_request(self, sender_id: int, request_id: int) -> None:
        request = await self.repo.get_request_by_id(request_id)
        if request is None:
            raise FittbotHTTPException(
                status_code=404, detail="Request not found",
                error_code="GYMMATE_FR_NOT_FOUND",
                log_data={"client_id": sender_id, "request_id": request_id},
            )
        try:
            request.cancel(sender_id)
        except d.NotSender as exc:
            raise FittbotHTTPException(
                status_code=403, detail=str(exc),
                error_code="GYMMATE_FR_FORBIDDEN",
                log_data={"client_id": sender_id, "request_id": request_id},
            )
        except d.RequestNotPending as exc:
            raise FittbotHTTPException(
                status_code=400, detail=str(exc),
                error_code="GYMMATE_FR_BAD_STATE",
                log_data={"client_id": sender_id, "request_id": request_id},
            )

        await self.repo.update_request_status(
            request_id, request.status.value, request.responded_at,
        )
        await self.bus.publish(FriendRequestCancelled(
            request_id=request_id,
            from_client_id=request.from_client_id,
            to_client_id=request.to_client_id,
        ))
        await self._fire(request.from_client_id, request.to_client_id)

    async def _block_set_for(self, client_id: int) -> set:

        return await self.repo.get_bidirectional_block_ids(client_id)

    async def list_incoming(self, client_id: int) -> List[dto.IncomingRequestDTO]:
        rows = await self.repo.list_incoming_requests(client_id)
        blocked = await self._block_set_for(client_id)
        out = []
        for r in rows:
            sender = r.get("other_client_id")
            if sender in blocked:
                continue
            r["other_avatar_url"] = _avatar_url_or_none(r.get("other_avatar_url"))
            out.append(dto.IncomingRequestDTO(**r))
        return out

    async def list_outgoing(self, client_id: int) -> List[dto.OutgoingRequestDTO]:
        rows = await self.repo.list_outgoing_requests(client_id)
        blocked = await self._block_set_for(client_id)
        out = []
        for r in rows:
            recipient = r.get("other_client_id")
            if recipient in blocked:
                continue
            r["other_avatar_url"] = _avatar_url_or_none(r.get("other_avatar_url"))
            out.append(dto.OutgoingRequestDTO(**r))
        return out

    async def list_friends(self, client_id: int) -> List[dto.FriendDTO]:
        rows = await self.repo.list_friends(client_id)
        blocked = await self._block_set_for(client_id)
        out = []
        for r in rows:
            if r.get("client_id") in blocked:
                continue
            r["avatar_url"] = _avatar_url_or_none(r.get("avatar_url"))
            out.append(dto.FriendDTO(**r))
        return out

    async def recent_request_sender_avatars(
        self, client_id: int, limit: int = 3,
    ) -> List[str]:
        """Top-N DPs of pending friend-request senders, wrapped as full
        CDN URLs. Used by gym_mate home to render the avatar stack next
        to the friend-requests badge."""
        raw = await self.repo.recent_request_sender_avatars(
            client_id=client_id, limit=limit,
        )
        wrapped = [_avatar_url_or_none(a) for a in raw]
        return [a for a in wrapped if a]

    async def list_mutual_friends(
        self, viewer_id: int, target_id: int, limit: int = 3,
    ) -> List[dto.MutualFriendDTO]:
        rows = await self.repo.list_mutual_friends(viewer_id, target_id, limit)
        # Hide any mutual that the viewer has blocked (in either direction)
        # — they don't want to see that person anywhere.
        blocked = await self._block_set_for(viewer_id)
        out = []
        for r in rows:
            if r.get("client_id") in blocked:
                continue
            r["avatar_url"] = _avatar_url_or_none(r.get("avatar_url"))
            out.append(dto.MutualFriendDTO(**r))
        return out

    async def get_relationship(
        self, viewer_id: int, target_id: int,
    ) -> dto.RelationshipDTO:
        row = await self.repo.get_relationship(viewer_id, target_id)
        return dto.RelationshipDTO(**row)

    async def get_onboarding_step2_suggestions(
        self, client_id: int,
    ) -> List[dto.OnboardingSuggestionDTO]:
        """Picks gym-mate cards to surface on the Step 2 response so a
        freshly onboarded user lands on a populated 'find friends'
        screen instead of an empty one.

        Logic:
          - If any match-qualifying candidates exist (>= MATCH_THRESHOLD
            on the goal/timing/personality/interests waterfall), pick
            up to 3 RANDOM ones (not top-3 by score — variety beats
            ranking on the very first screen).
          - If zero matches, fall back to up to 9 random recently-
            onboarded users (no signal required).
        Blocks / existing friends / pending requests are still excluded
        via the standard hard-exclusion set."""
        import random

        my_profile = await self.repo.get_my_profile(client_id)
        if my_profile is None:
            # Step 2 just completed; this shouldn't fail. Defensive [].
            return []

        exclude = await self.repo.get_exclusion_set(client_id)

        matches = await self.repo.find_match_candidates(
            viewer_id=client_id,
            my_profile=my_profile,
            exclude_ids=exclude,
            # Wide pool so random.sample has variety on every refresh.
            limit=50,
        )
        if matches:
            sample_n = min(3, len(matches))
            chosen = random.sample(matches, sample_n)
        else:
            # No matches — broaden to random recent onboarders.
            pool = await self.repo.find_fallback_candidates(
                viewer_id=client_id,
                exclude_ids=exclude,
                # Pull a wide pool then sample, so we get random not
                # just newest-9.
                limit=50,
            )
            sample_n = min(9, len(pool))
            chosen = random.sample(pool, sample_n) if pool else []

        return [
            dto.OnboardingSuggestionDTO(
                client_id=c["client_id"],
                name=c.get("name"),
                avatar_url=_avatar_url_or_none(c.get("avatar_url")),
            )
            for c in chosen
        ]

    async def get_match_info(
        self, viewer_id: int, target_id: int,
    ) -> Optional[dto.MatchInfoDTO]:
        """Two-key match block:
            - percentage: viewer ↔ target compatibility score (same
              weights as friend_suggestions[type=match])
            - goals: the target's full profile selections (same chip
              ordering as `details`)
        Returns None if either side hasn't completed onboarding.
        """
        if viewer_id == target_id:
            return None
        from app.fittbot_api.v2.Fymble.gym_mate.profile import (
            build_profile_details,
        )
        from ._repository import (
            W_GOAL, W_CITY, W_TIMING, W_PERSONALITY,
            W_PER_INTEREST, W_INTEREST_CAP,
        )
        viewer = await self.repo.get_my_profile(viewer_id)
        target = await self.repo.get_my_profile(target_id)
        if viewer is None or target is None:
            return None

        score = 0
        if viewer["primary_goal"] == target["primary_goal"]:
            score += W_GOAL
        # City only counts when BOTH sides set one and they match
        # (case-insensitive). Missing city => no bonus, no penalty.
        viewer_city = (viewer.get("city") or "").strip().lower()
        target_city = (target.get("city") or "").strip().lower()
        if viewer_city and target_city and viewer_city == target_city:
            score += W_CITY
        if viewer["preferred_timing"] == target["preferred_timing"]:
            score += W_TIMING
        if viewer["gym_personality"] == target["gym_personality"]:
            score += W_PERSONALITY
        overlap = len(
            set(viewer.get("activity_interests") or [])
            & set(target.get("activity_interests") or [])
        )
        score += min(overlap * W_PER_INTEREST, W_INTEREST_CAP)

        return dto.MatchInfoDTO(
            percentage=min(score, 100),
            goals=build_profile_details(
                target["primary_goal"],
                target.get("activity_interests") or [],
                target["preferred_timing"],
                target["gym_personality"],
            ),
        )

    async def unfriend(self, client_id: int, other_id: int) -> None:
        removed = await self.repo.remove_friendship(client_id, other_id)
        if not removed:
            raise FittbotHTTPException(
                status_code=404, detail="Not friends",
                error_code="GYMMATE_FR_NOT_FOUND",
                log_data={"client_id": client_id, "other": other_id},
            )
        ca, cb = d.canonical_pair(client_id, other_id)
        await self.bus.publish(FriendRemoved(client_a_id=ca, client_b_id=cb))
        await self._fire(client_id, other_id)

    # ── Mappers ───────────────────────────────────────────────

    @staticmethod
    def _to_suggestion_dtos(rows: List[dict]) -> List[dto.FriendSuggestionDTO]:
        return [
            dto.FriendSuggestionDTO(
                sno=i,
                client_id=r["client_id"],
                name=r.get("name"),
                avatar_url=_avatar_url_or_none(r.get("avatar_url")),
                bio=r.get("bio"),
                details=r.get("details") or [],
                suggestion_type=r["suggestion_type"],
                mutual_count=r.get("mutual_count"),
                match_percentage=r.get("match_percentage"),
            )
            for i, r in enumerate(rows, start=1)
        ]

    @staticmethod
    def _to_discover_dtos(rows: List[dict]) -> List[dto.DiscoverProfileDTO]:
        return [
            dto.DiscoverProfileDTO(
                sno=i,
                client_id=r["client_id"],
                name=r.get("name"),
                avatar_url=_avatar_url_or_none(r.get("avatar_url")),
                bio=r.get("bio"),
                details=r.get("details") or [],
                city=r.get("city"),
                suggestion_type=r["suggestion_type"],
                match_percentage=r.get("match_percentage"),
            )
            for i, r in enumerate(rows, start=1)
        ]

    @staticmethod
    def _to_request_dto(request: d.FriendRequest) -> dto.FriendRequestDTO:
        return dto.FriendRequestDTO(
            request_id=request.id,
            from_client_id=request.from_client_id,
            to_client_id=request.to_client_id,
            status=request.status.value,
            created_at=request.created_at,
            responded_at=request.responded_at,
        )
