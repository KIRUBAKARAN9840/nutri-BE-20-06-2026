from collections import Counter
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Set

from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.fittbot_api.v2.Fymble.gym_mate.profile import build_profile_details
from app.models.fittbot_models.client import Client as ClientORM
from app.models.fittbot_models.gymmate import (
    GymMateBlock as BlockORM,
    GymMateFriendRequest as FriendRequestORM,
    GymMateFriendship as FriendshipORM,
    GymMateProfile as ProfileORM,
    GymMateProfilePhoto as PhotoORM,
)

from . import _domain as d



W_GOAL = 30
W_CITY = 30               # same city — only counts when BOTH sides set a city
W_TIMING = 20
W_PERSONALITY = 10
W_PER_INTEREST = 5
W_INTEREST_CAP = 10       # up to 2 overlapping interests count
MATCH_THRESHOLD = 30      # min score to be returned as "match" (one strong signal)


class FriendRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_my_profile(self, client_id: int) -> Optional[dict]:
        stmt = (
            select(
                ClientORM.gym_id,
                ProfileORM.primary_goal,
                ProfileORM.preferred_timing,
                ProfileORM.gym_personality,
                ProfileORM.activity_interests,
                ProfileORM.city,
            )
            .select_from(ClientORM)
            .join(ProfileORM, ProfileORM.client_id == ClientORM.client_id)
            .where(
                ClientORM.client_id == client_id,
                ProfileORM.onboarding_completed.is_(True),
            )
        )
        row = (await self.db.execute(stmt)).first()
        if row is None:
            return None
        return {
            "gym_id": row.gym_id,
            "primary_goal": row.primary_goal,
            "preferred_timing": row.preferred_timing,
            "gym_personality": row.gym_personality,
            "activity_interests": list(row.activity_interests or []),
            "city": row.city,
        }



    async def get_exclusion_set(self, viewer_id: int) -> Set[int]:

        out: Set[int] = {viewer_id}

        # Friends
        f_rows = (await self.db.execute(
            select(FriendshipORM.client_a_id, FriendshipORM.client_b_id).where(
                or_(
                    FriendshipORM.client_a_id == viewer_id,
                    FriendshipORM.client_b_id == viewer_id,
                )
            )
        )).all()
        for a, b in f_rows:
            out.add(a if a != viewer_id else b)

        # Pending friend requests
        fr_rows = (await self.db.execute(
            select(FriendRequestORM.from_client_id, FriendRequestORM.to_client_id).where(
                FriendRequestORM.status == "pending",
                or_(
                    FriendRequestORM.from_client_id == viewer_id,
                    FriendRequestORM.to_client_id == viewer_id,
                ),
            )
        )).all()
        for fr, to in fr_rows:
            out.add(fr if fr != viewer_id else to)

        # Blocks (either direction)
        b_rows = (await self.db.execute(
            select(BlockORM.blocker_client_id, BlockORM.blocked_client_id).where(
                or_(
                    BlockORM.blocker_client_id == viewer_id,
                    BlockORM.blocked_client_id == viewer_id,
                )
            )
        )).all()
        for blocker, blocked in b_rows:
            out.add(blocker if blocker != viewer_id else blocked)

        return out

    async def get_bidirectional_block_ids(self, client_id: int) -> set:
        """All client_ids in a block pair with this client, either side.
        Shared helper for all friends read paths."""
        rows = (await self.db.execute(
            select(BlockORM.blocker_client_id, BlockORM.blocked_client_id).where(
                or_(
                    BlockORM.blocker_client_id == client_id,
                    BlockORM.blocked_client_id == client_id,
                )
            )
        )).all()
        out: set = set()
        for blocker, blocked in rows:
            out.add(blocked if blocker == client_id else blocker)
        return out

    # ── Tier 1: mutual friends ────────────────────────────────

    async def find_mutual_candidates(
        self,
        viewer_id: int,
        exclude_ids: Set[int],
        limit: int = 5,
    ) -> List[dict]:
        """Friends-of-friends ranked by mutual count, enriched with
        name + DP + primary_goal. Returns at most `limit` entries."""
        my_friends = await self._get_friend_ids(viewer_id)
        if not my_friends:
            return []

        # Pull every friendship row that touches one of my friends.
        rows = (await self.db.execute(
            select(FriendshipORM.client_a_id, FriendshipORM.client_b_id).where(
                or_(
                    FriendshipORM.client_a_id.in_(my_friends),
                    FriendshipORM.client_b_id.in_(my_friends),
                )
            )
        )).all()

        my_friends_set = set(my_friends)
        mutuals: Counter = Counter()
        for a, b in rows:
            # Determine which side is my friend and which is the candidate.
            if a in my_friends_set and b not in my_friends_set:
                cand = b
            elif b in my_friends_set and a not in my_friends_set:
                cand = a
            else:
                # both sides are my friends (i.e., two of my friends are friends with each other) — skip
                continue
            if cand in exclude_ids:
                continue
            mutuals[cand] += 1

        if not mutuals:
            return []

        ranked = [cid for cid, _ in mutuals.most_common(limit)]
        enriched = await self._enrich(ranked)
        # Attach mutual_count + preserve mutual ordering
        out: List[dict] = []
        for cid in ranked:
            row = enriched.get(cid)
            if row is None:
                continue
            row["mutual_count"] = mutuals[cid]
            out.append(row)
        return out[:limit]

    # ── Tier 2: profile match ─────────────────────────────────

    async def find_match_candidates(
        self,
        viewer_id: int,
        my_profile: dict,
        exclude_ids: Set[int],
        limit: int = 5,
    ) -> List[dict]:
        """Profile-based similarity. Returns at most `limit` entries
        scoring >= MATCH_THRESHOLD."""
        # Partial score is computed in SQL via CASE; the activity-interest
        # overlap is computed in Python after fetching candidates (JSON
        # column comparisons are clumsy and slow in SQL).
        #
        # City only contributes when the VIEWER has a city set — otherwise
        # we'd be comparing against NULL (which would either never match or,
        # worse, lump all city-less users together). Missing city => no
        # bonus, no penalty. Matched case-insensitively.
        my_city = (my_profile.get("city") or "").strip()
        score_terms = [
            case((ProfileORM.primary_goal == my_profile["primary_goal"], W_GOAL), else_=0),
            case((ProfileORM.preferred_timing == my_profile["preferred_timing"], W_TIMING), else_=0),
            case((ProfileORM.gym_personality == my_profile["gym_personality"], W_PERSONALITY), else_=0),
        ]
        signal_terms = [
            ProfileORM.primary_goal == my_profile["primary_goal"],
            ProfileORM.preferred_timing == my_profile["preferred_timing"],
            ProfileORM.gym_personality == my_profile["gym_personality"],
        ]
        if my_city:
            city_match = func.lower(ProfileORM.city) == my_city.lower()
            score_terms.append(case((city_match, W_CITY), else_=0))
            signal_terms.append(city_match)

        partial_score_expr = sum(
            score_terms[1:], score_terms[0]
        ).label("partial_score")

        # Narrow the pool: candidate must share at least one profile
        # signal — otherwise their partial_score would be 0 (no match
        # possible even after the interest overlap maxes out at the
        # cap, below the threshold for the strongest single-signal
        # qualifier).
        any_signal = or_(*signal_terms)

        stmt = (
            select(
                ClientORM.client_id,
                ClientORM.name,
                ClientORM.profile,
                ProfileORM.primary_goal,
                ProfileORM.activity_interests,
                ProfileORM.preferred_timing,
                ProfileORM.gym_personality,
                ProfileORM.bio,
                ProfileORM.city,
                PhotoORM.s3_path.label("dp"),
                partial_score_expr,
            )
            .select_from(ClientORM)
            .join(ProfileORM, ProfileORM.client_id == ClientORM.client_id)
            .outerjoin(
                PhotoORM,
                and_(
                    PhotoORM.profile_id == ProfileORM.id,
                    PhotoORM.is_primary.is_(True),
                ),
            )
            .where(
                ProfileORM.onboarding_completed.is_(True),
                ~ClientORM.client_id.in_(exclude_ids),
                any_signal,
            )
            .order_by(partial_score_expr.desc())
            .limit(50)
        )
        rows = (await self.db.execute(stmt)).all()

        my_interests = set(my_profile.get("activity_interests") or [])
        scored: List[dict] = []
        for r in rows:
            their_interests = set(r.activity_interests or [])
            overlap = len(my_interests & their_interests)
            interest_score = min(overlap * W_PER_INTEREST, W_INTEREST_CAP)
            total = int(r.partial_score) + interest_score
            if total < MATCH_THRESHOLD:
                continue
            scored.append({
                "client_id": r.client_id,
                "name": r.name,
                "avatar_url": r.dp,
                "bio": r.bio,
                "city": r.city,
                "details": build_profile_details(
                    r.primary_goal,
                    r.activity_interests,
                    r.preferred_timing,
                    r.gym_personality,
                ),
                "match_percentage": min(total, 100),
            })
        scored.sort(key=lambda x: x["match_percentage"], reverse=True)
        return scored[:limit]

    # ── Tier 3: fallback (any onboarded user) ─────────────────

    async def find_fallback_candidates(
        self,
        viewer_id: int,
        exclude_ids: Set[int],
        limit: int = 5,
    ) -> List[dict]:
        """Most-recently-onboarded users that don't fail the
        hard-exclusion filter. No ranking signals."""
        stmt = (
            select(
                ClientORM.client_id,
                ClientORM.name,
                ClientORM.profile,
                ProfileORM.primary_goal,
                ProfileORM.activity_interests,
                ProfileORM.preferred_timing,
                ProfileORM.gym_personality,
                ProfileORM.bio,
                ProfileORM.city,
                PhotoORM.s3_path.label("dp"),
            )
            .select_from(ClientORM)
            .join(ProfileORM, ProfileORM.client_id == ClientORM.client_id)
            .outerjoin(
                PhotoORM,
                and_(
                    PhotoORM.profile_id == ProfileORM.id,
                    PhotoORM.is_primary.is_(True),
                ),
            )
            .where(
                ProfileORM.onboarding_completed.is_(True),
                ~ClientORM.client_id.in_(exclude_ids),
            )
            .order_by(ProfileORM.created_at.desc())
            .limit(limit)
        )
        rows = (await self.db.execute(stmt)).all()
        return [self._candidate_row_to_dict(r) for r in rows]

    # ── Discover deck: same-city tier ─────────────────────────

    async def find_same_city_candidates(
        self,
        viewer_id: int,
        city: str,
        exclude_ids: Set[int],
        limit: int = 30,
    ) -> List[dict]:
        """Onboarded users in the same city (case-insensitive), newest
        first. Used by the swipe-to-connect deck after the match tier.
        Caller must pass a non-empty `city` — viewers with no city skip
        this tier entirely."""
        stmt = (
            select(
                ClientORM.client_id,
                ClientORM.name,
                ClientORM.profile,
                ProfileORM.primary_goal,
                ProfileORM.activity_interests,
                ProfileORM.preferred_timing,
                ProfileORM.gym_personality,
                ProfileORM.bio,
                ProfileORM.city,
                PhotoORM.s3_path.label("dp"),
            )
            .select_from(ClientORM)
            .join(ProfileORM, ProfileORM.client_id == ClientORM.client_id)
            .outerjoin(
                PhotoORM,
                and_(
                    PhotoORM.profile_id == ProfileORM.id,
                    PhotoORM.is_primary.is_(True),
                ),
            )
            .where(
                ProfileORM.onboarding_completed.is_(True),
                ~ClientORM.client_id.in_(exclude_ids),
                func.lower(ProfileORM.city) == city.strip().lower(),
            )
            # Photo-having profiles first, then newest.
            .order_by(PhotoORM.s3_path.is_(None), ProfileORM.created_at.desc())
            .limit(limit)
        )
        rows = (await self.db.execute(stmt)).all()
        return [self._candidate_row_to_dict(r) for r in rows]

    # ── Discover deck: recent-profiles pool ───────────────────

    async def find_recent_candidates(
        self,
        viewer_id: int,
        exclude_ids: Set[int],
        pool_size: int = 50,
    ) -> List[dict]:
        """The newest `pool_size` onboarded profiles (or however many
        exist). The service random-samples from this pool so brand-new
        users still get a varied, populated deck."""
        stmt = (
            select(
                ClientORM.client_id,
                ClientORM.name,
                ClientORM.profile,
                ProfileORM.primary_goal,
                ProfileORM.activity_interests,
                ProfileORM.preferred_timing,
                ProfileORM.gym_personality,
                ProfileORM.bio,
                ProfileORM.city,
                PhotoORM.s3_path.label("dp"),
            )
            .select_from(ClientORM)
            .join(ProfileORM, ProfileORM.client_id == ClientORM.client_id)
            .outerjoin(
                PhotoORM,
                and_(
                    PhotoORM.profile_id == ProfileORM.id,
                    PhotoORM.is_primary.is_(True),
                ),
            )
            .where(
                ProfileORM.onboarding_completed.is_(True),
                ~ClientORM.client_id.in_(exclude_ids),
            )
            # Photo-having profiles first, then newest. discover_profiles
            # relies on this ordering to fill the deck with photo profiles
            # before any empty ones.
            .order_by(PhotoORM.s3_path.is_(None), ProfileORM.created_at.desc())
            .limit(pool_size)
        )
        rows = (await self.db.execute(stmt)).all()
        return [self._candidate_row_to_dict(r) for r in rows]

    @staticmethod
    def _candidate_row_to_dict(r) -> dict:
        """Shared row → dict mapper for the deck/fallback tiers (everything
        except match_percentage, which only the match tier computes)."""
        return {
            "client_id": r.client_id,
            "name": r.name,
            "avatar_url": r.dp,
            "bio": r.bio,
            "city": r.city,
            "details": build_profile_details(
                r.primary_goal,
                r.activity_interests,
                r.preferred_timing,
                r.gym_personality,
            ),
        }

    # ── Internals ─────────────────────────────────────────────

    async def get_relationship(
        self, viewer_id: int, target_id: int,
    ) -> dict:
        """Returns the viewer ↔ target relationship state.

        Output: {"status": str, "request_id": Optional[int]}
        Status priority: friends > request_received > request_sent > none.
        """
        if viewer_id == target_id:
            return {"status": "none", "request_id": None}

        if await self.is_friend(viewer_id, target_id):
            return {"status": "friends", "request_id": None}

        stmt = (
            select(
                FriendRequestORM.id,
                FriendRequestORM.from_client_id,
            )
            .where(
                FriendRequestORM.status == "pending",
                or_(
                    and_(
                        FriendRequestORM.from_client_id == viewer_id,
                        FriendRequestORM.to_client_id == target_id,
                    ),
                    and_(
                        FriendRequestORM.from_client_id == target_id,
                        FriendRequestORM.to_client_id == viewer_id,
                    ),
                ),
            )
            .limit(1)
        )
        row = (await self.db.execute(stmt)).first()
        if row is None:
            return {"status": "none", "request_id": None}
        if row.from_client_id == viewer_id:
            return {"status": "request_sent", "request_id": row.id}
        return {"status": "request_received", "request_id": row.id}

    async def list_mutual_friends(
        self, viewer_id: int, target_id: int, limit: int = 3,
    ) -> List[dict]:
        """Up to `limit` clients who are friends of BOTH viewer and target.

        Returns slim {client_id, name, avatar_url} — used on someone
        else's profile screen ("3 mutual friends" badge).
        """
        if viewer_id == target_id:
            return []
        viewer_friends = set(await self._get_friend_ids(viewer_id))
        if not viewer_friends:
            return []
        target_friends = set(await self._get_friend_ids(target_id))
        mutual_ids = list(viewer_friends & target_friends)
        if not mutual_ids:
            return []

        picked = mutual_ids[:limit]
        stmt = (
            select(
                ClientORM.client_id,
                ClientORM.name,
                ClientORM.profile,
                PhotoORM.s3_path.label("dp"),
            )
            .select_from(ClientORM)
            .outerjoin(ProfileORM, ProfileORM.client_id == ClientORM.client_id)
            .outerjoin(
                PhotoORM,
                and_(
                    PhotoORM.profile_id == ProfileORM.id,
                    PhotoORM.is_primary.is_(True),
                ),
            )
            .where(ClientORM.client_id.in_(picked))
        )
        rows = (await self.db.execute(stmt)).all()
        by_id = {
            r.client_id: {
                "client_id": r.client_id,
                "name": r.name,
                "avatar_url": r.dp,
            }
            for r in rows
        }
        # Preserve the mutual_ids order
        return [by_id[cid] for cid in picked if cid in by_id]

    async def _get_friend_ids(self, viewer_id: int) -> List[int]:
        rows = (await self.db.execute(
            select(FriendshipORM.client_a_id, FriendshipORM.client_b_id).where(
                or_(
                    FriendshipORM.client_a_id == viewer_id,
                    FriendshipORM.client_b_id == viewer_id,
                )
            )
        )).all()
        return [a if a != viewer_id else b for a, b in rows]

    async def _enrich(self, client_ids: Iterable[int]) -> Dict[int, dict]:
        ids = list(client_ids)
        if not ids:
            return {}
        stmt = (
            select(
                ClientORM.client_id,
                ClientORM.name,
                ClientORM.profile,
                ProfileORM.primary_goal,
                ProfileORM.activity_interests,
                ProfileORM.preferred_timing,
                ProfileORM.gym_personality,
                ProfileORM.bio,
                PhotoORM.s3_path.label("dp"),
            )
            .select_from(ClientORM)
            .join(ProfileORM, ProfileORM.client_id == ClientORM.client_id)
            .outerjoin(
                PhotoORM,
                and_(
                    PhotoORM.profile_id == ProfileORM.id,
                    PhotoORM.is_primary.is_(True),
                ),
            )
            .where(
                ClientORM.client_id.in_(ids),
                ProfileORM.onboarding_completed.is_(True),
            )
        )
        rows = (await self.db.execute(stmt)).all()
        return {
            r.client_id: {
                "client_id": r.client_id,
                "name": r.name,
                "avatar_url": r.dp,
                "bio": r.bio,
                "details": build_profile_details(
                    r.primary_goal,
                    r.activity_interests,
                    r.preferred_timing,
                    r.gym_personality,
                ),
            }
            for r in rows
        }

    # ── Friend-request lifecycle queries ──────────────────────

    async def is_recipient_onboarded(self, client_id: int) -> bool:
        """Recipient must have completed the gym_mate onboarding flow."""
        stmt = select(ProfileORM.onboarding_completed).where(
            ProfileORM.client_id == client_id
        )
        row = (await self.db.execute(stmt)).first()
        return bool(row and row.onboarding_completed)

    async def is_blocked_either_way(self, a: int, b: int) -> bool:
        stmt = (
            select(BlockORM.id)
            .where(
                or_(
                    and_(BlockORM.blocker_client_id == a, BlockORM.blocked_client_id == b),
                    and_(BlockORM.blocker_client_id == b, BlockORM.blocked_client_id == a),
                )
            )
            .limit(1)
        )
        return (await self.db.execute(stmt)).first() is not None

    async def is_friend(self, a: int, b: int) -> bool:
        ca, cb = d.canonical_pair(a, b)
        stmt = (
            select(FriendshipORM.id)
            .where(
                FriendshipORM.client_a_id == ca,
                FriendshipORM.client_b_id == cb,
            )
            .limit(1)
        )
        return (await self.db.execute(stmt)).first() is not None

    async def get_pending_either_direction(
        self, a: int, b: int,
    ) -> Optional[d.FriendRequest]:
        """A pending request between two users in either direction."""
        stmt = (
            select(FriendRequestORM)
            .where(
                FriendRequestORM.status == "pending",
                or_(
                    and_(
                        FriendRequestORM.from_client_id == a,
                        FriendRequestORM.to_client_id == b,
                    ),
                    and_(
                        FriendRequestORM.from_client_id == b,
                        FriendRequestORM.to_client_id == a,
                    ),
                ),
            )
            .limit(1)
        )
        row = (await self.db.execute(stmt)).scalar_one_or_none()
        return self._fr_to_domain(row) if row else None

    async def add_request(self, request: d.FriendRequest) -> d.FriendRequest:
        """Insert a pending request.

        Uses ON DUPLICATE KEY UPDATE so a previously cancelled/rejected
        request between the same (from, to) pair can be reopened by the
        same sender (the UNIQUE(from, to) constraint would otherwise
        reject it). The status is reset to 'pending' and timestamps
        are refreshed.
        """
        stmt = (
            mysql_insert(FriendRequestORM)
            .values(
                from_client_id=request.from_client_id,
                to_client_id=request.to_client_id,
                status="pending",
                created_at=datetime.now(),
                responded_at=None,
            )
        )
        stmt = stmt.on_duplicate_key_update(
            status="pending",
            created_at=datetime.now(),
            responded_at=None,
        )
        await self.db.execute(stmt)
        # Re-read to get the row's id + created_at consistently.
        row = (await self.db.execute(
            select(FriendRequestORM).where(
                FriendRequestORM.from_client_id == request.from_client_id,
                FriendRequestORM.to_client_id == request.to_client_id,
            )
        )).scalar_one_or_none()
        return self._fr_to_domain(row)

    async def get_request_by_id(self, request_id: int) -> Optional[d.FriendRequest]:
        row = (await self.db.execute(
            select(FriendRequestORM).where(FriendRequestORM.id == request_id)
        )).scalar_one_or_none()
        return self._fr_to_domain(row) if row else None

    async def update_request_status(
        self, request_id: int, status: str, responded_at: Optional[datetime],
    ) -> None:
        await self.db.execute(
            update(FriendRequestORM)
            .where(FriendRequestORM.id == request_id)
            .values(status=status, responded_at=responded_at)
        )

    # ── Friendship CRUD ───────────────────────────────────────

    async def add_friendship(self, a: int, b: int) -> None:
        """Insert a canonical friendship pair (smaller, larger).
        UNIQUE constraint makes this idempotent."""
        ca, cb = d.canonical_pair(a, b)
        stmt = mysql_insert(FriendshipORM).values(
            client_a_id=ca, client_b_id=cb, created_at=datetime.now(),
        )
        stmt = stmt.prefix_with("IGNORE")
        await self.db.execute(stmt)

    async def remove_friendship(self, a: int, b: int) -> bool:
        ca, cb = d.canonical_pair(a, b)
        result = await self.db.execute(
            delete(FriendshipORM).where(
                FriendshipORM.client_a_id == ca,
                FriendshipORM.client_b_id == cb,
            )
        )
        return (result.rowcount or 0) > 0

    async def recent_request_sender_avatars(
        self, client_id: int, limit: int = 3,
    ) -> List[str]:
        """Top-N DPs of the most-recent pending friend-request senders.
        Avatar precedence: gym_mate primary photo → clients.profile.
        Used by the home page to render an avatar stack next to the
        'X friend requests' badge. NULLs filtered out so the FE never
        renders broken images."""
        stmt = (
            select(
                func.coalesce(PhotoORM.s3_path, ClientORM.profile)
                    .label("avatar"),
            )
            .select_from(FriendRequestORM)
            .join(ClientORM, ClientORM.client_id == FriendRequestORM.from_client_id)
            .outerjoin(
                ProfileORM, ProfileORM.client_id == FriendRequestORM.from_client_id,
            )
            .outerjoin(
                PhotoORM,
                and_(
                    PhotoORM.profile_id == ProfileORM.id,
                    PhotoORM.is_primary.is_(True),
                ),
            )
            .where(
                FriendRequestORM.to_client_id == client_id,
                FriendRequestORM.status == "pending",
            )
            .order_by(FriendRequestORM.created_at.desc())
            .limit(limit)
        )
        rows = (await self.db.execute(stmt)).all()
        return [r.avatar for r in rows if r.avatar]

    # ── Listings ──────────────────────────────────────────────

    async def list_incoming_requests(
        self, viewer_id: int, limit: int = 50, offset: int = 0,
    ) -> List[dict]:
        """Pending requests TO me, with sender display info."""
        stmt = (
            select(
                FriendRequestORM.id.label("request_id"),
                FriendRequestORM.from_client_id.label("other_id"),
                FriendRequestORM.created_at,
                ClientORM.name,
                ClientORM.profile,
                ProfileORM.primary_goal,
                PhotoORM.s3_path.label("dp"),
            )
            .select_from(FriendRequestORM)
            .join(ClientORM, ClientORM.client_id == FriendRequestORM.from_client_id)
            .outerjoin(ProfileORM, ProfileORM.client_id == ClientORM.client_id)
            .outerjoin(
                PhotoORM,
                and_(
                    PhotoORM.profile_id == ProfileORM.id,
                    PhotoORM.is_primary.is_(True),
                ),
            )
            .where(
                FriendRequestORM.to_client_id == viewer_id,
                FriendRequestORM.status == "pending",
            )
            .order_by(FriendRequestORM.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.db.execute(stmt)).all()
        return [
            {
                "request_id": r.request_id,
                "other_client_id": r.other_id,
                "other_name": r.name,
                "other_avatar_url": r.dp,
                "other_primary_goal": r.primary_goal,
                "created_at": r.created_at,
            }
            for r in rows
        ]

    async def list_outgoing_requests(
        self, viewer_id: int, limit: int = 50, offset: int = 0,
    ) -> List[dict]:
        """Pending requests FROM me, with recipient display info."""
        stmt = (
            select(
                FriendRequestORM.id.label("request_id"),
                FriendRequestORM.to_client_id.label("other_id"),
                FriendRequestORM.created_at,
                ClientORM.name,
                ClientORM.profile,
                ProfileORM.primary_goal,
                PhotoORM.s3_path.label("dp"),
            )
            .select_from(FriendRequestORM)
            .join(ClientORM, ClientORM.client_id == FriendRequestORM.to_client_id)
            .outerjoin(ProfileORM, ProfileORM.client_id == ClientORM.client_id)
            .outerjoin(
                PhotoORM,
                and_(
                    PhotoORM.profile_id == ProfileORM.id,
                    PhotoORM.is_primary.is_(True),
                ),
            )
            .where(
                FriendRequestORM.from_client_id == viewer_id,
                FriendRequestORM.status == "pending",
            )
            .order_by(FriendRequestORM.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.db.execute(stmt)).all()
        return [
            {
                "request_id": r.request_id,
                "other_client_id": r.other_id,
                "other_name": r.name,
                "other_avatar_url": r.dp,
                "other_primary_goal": r.primary_goal,
                "created_at": r.created_at,
            }
            for r in rows
        ]

    async def list_friends(
        self, viewer_id: int, limit: int = 100, offset: int = 0,
    ) -> List[dict]:
        """My current friends, ordered newest-first."""
        other_id_expr = case(
            (FriendshipORM.client_a_id == viewer_id, FriendshipORM.client_b_id),
            else_=FriendshipORM.client_a_id,
        ).label("other_id")

        # Two-stage: first pull the friend ids + the friendship.created_at
        # so we can join clients/profile in a second pass keeping order.
        rows = (await self.db.execute(
            select(other_id_expr, FriendshipORM.created_at)
            .where(
                or_(
                    FriendshipORM.client_a_id == viewer_id,
                    FriendshipORM.client_b_id == viewer_id,
                )
            )
            .order_by(FriendshipORM.created_at.desc())
            .limit(limit)
            .offset(offset)
        )).all()
        if not rows:
            return []
        ids_in_order = [r.other_id for r in rows]
        friended_at_map = {r.other_id: r.created_at for r in rows}

        enriched = await self._enrich(ids_in_order)
        out = []
        for cid in ids_in_order:
            row = enriched.get(cid)
            if row is None:
                # No gym_mate profile (rare, but possible if the friendship
                # predates onboarding). Fall back to clients-only display.
                client_row = (await self.db.execute(
                    select(ClientORM.client_id, ClientORM.name, ClientORM.profile)
                    .where(ClientORM.client_id == cid)
                )).first()
                if client_row is None:
                    continue
                row = {
                    "client_id": cid,
                    "name": client_row.name,
                    # No gym_mate photo → send None (don't leak the main-app pic).
                    "avatar_url": None,
                    "primary_goal": None,
                }
            row["friended_at"] = friended_at_map.get(cid)
            out.append(row)
        return out

    # ── Mappers ───────────────────────────────────────────────

    @staticmethod
    def _fr_to_domain(row: FriendRequestORM) -> d.FriendRequest:
        return d.FriendRequest(
            id=row.id,
            from_client_id=row.from_client_id,
            to_client_id=row.to_client_id,
            status=d.FriendRequestStatus(row.status),
            created_at=row.created_at,
            responded_at=row.responded_at,
        )
