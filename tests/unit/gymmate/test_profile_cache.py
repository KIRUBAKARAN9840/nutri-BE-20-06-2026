"""Tests for the Redis cache adapter — _cache.ProfileCache.

Uses fakeredis (from the project's conftest.py) — no real Redis instance.
Verifies get/set roundtrips, TTL is applied, invalidate clears all
related keys, and a missing/corrupt cache entry misses cleanly.
"""

import pytest

from app.fittbot_api.v2.Fymble.gym_mate.profile._cache import (
    ProfileCache,
    TTL_MATCH_ATTRS_SECONDS,
    TTL_STATUS_SECONDS,
    TTL_SUMMARY_SECONDS,
    _match_attrs_key,
    _status_key,
    _summary_key,
)
from app.fittbot_api.v2.Fymble.gym_mate.profile.schemas import (
    OnboardingStatusDTO,
    ProfileMatchAttributesDTO,
    ProfileSummaryDTO,
)


# ---------------------------------------------------------------------------
# Status cache
# ---------------------------------------------------------------------------
class TestStatusCache:
    @pytest.mark.asyncio
    async def test_miss_returns_none(self, fake_redis):
        cache = ProfileCache(fake_redis)
        assert await cache.get_status(42) is None

    @pytest.mark.asyncio
    async def test_set_then_get_roundtrips(self, fake_redis):
        cache = ProfileCache(fake_redis)
        original = OnboardingStatusDTO(
            client_id=42, next_step=2, onboarding_completed=False
        )
        await cache.set_status(original)
        got = await cache.get_status(42)
        assert got == original

    @pytest.mark.asyncio
    async def test_ttl_is_set(self, fake_redis):
        cache = ProfileCache(fake_redis)
        await cache.set_status(OnboardingStatusDTO(
            client_id=42, next_step=0, onboarding_completed=True
        ))
        ttl = await fake_redis.ttl(_status_key(42))
        assert 0 < ttl <= TTL_STATUS_SECONDS

    @pytest.mark.asyncio
    async def test_corrupt_cache_entry_is_purged(self, fake_redis):
        cache = ProfileCache(fake_redis)
        await fake_redis.set(_status_key(42), "{not json")
        got = await cache.get_status(42)
        assert got is None
        # Corrupt entry was deleted
        assert await fake_redis.get(_status_key(42)) is None


# ---------------------------------------------------------------------------
# Match attrs cache
# ---------------------------------------------------------------------------
class TestMatchAttrsCache:
    @pytest.mark.asyncio
    async def test_set_then_get_roundtrips(self, fake_redis):
        cache = ProfileCache(fake_redis)
        original = ProfileMatchAttributesDTO(
            client_id=1, primary_goal="endurance",
            activity_interests=["running", "cardio"],
            preferred_timing="early_morning",
            gym_personality="goal_chaser",
        )
        await cache.set_match_attrs(original)
        got = await cache.get_match_attrs(1)
        assert got == original

    @pytest.mark.asyncio
    async def test_ttl_is_set(self, fake_redis):
        cache = ProfileCache(fake_redis)
        await cache.set_match_attrs(ProfileMatchAttributesDTO(
            client_id=1, primary_goal="muscle_gain",
            activity_interests=["strength"],
            preferred_timing="morning",
            gym_personality="beast_mode",
        ))
        ttl = await fake_redis.ttl(_match_attrs_key(1))
        assert 0 < ttl <= TTL_MATCH_ATTRS_SECONDS


# ---------------------------------------------------------------------------
# Summary cache
# ---------------------------------------------------------------------------
class TestSummaryCache:
    @pytest.mark.asyncio
    async def test_set_then_get_roundtrips(self, fake_redis):
        cache = ProfileCache(fake_redis)
        original = ProfileSummaryDTO(
            client_id=1,
            primary_photo_url="gym_mate/profile/1/0_abc.jpg",
            bio="Hi",
            onboarding_completed=True,
        )
        await cache.set_summary(original)
        got = await cache.get_summary(1)
        assert got == original

    @pytest.mark.asyncio
    async def test_ttl_is_set(self, fake_redis):
        cache = ProfileCache(fake_redis)
        await cache.set_summary(ProfileSummaryDTO(
            client_id=1, onboarding_completed=False,
        ))
        ttl = await fake_redis.ttl(_summary_key(1))
        assert 0 < ttl <= TTL_SUMMARY_SECONDS


# ---------------------------------------------------------------------------
# Invalidation
# ---------------------------------------------------------------------------
class TestInvalidate:
    @pytest.mark.asyncio
    async def test_invalidate_clears_all_three_caches(self, fake_redis):
        cache = ProfileCache(fake_redis)

        await cache.set_status(OnboardingStatusDTO(
            client_id=1, next_step=0, onboarding_completed=True
        ))
        await cache.set_match_attrs(ProfileMatchAttributesDTO(
            client_id=1, primary_goal="muscle_gain",
            activity_interests=["strength"], preferred_timing="morning",
            gym_personality="beast_mode",
        ))
        await cache.set_summary(ProfileSummaryDTO(
            client_id=1, onboarding_completed=True,
        ))

        await cache.invalidate(1)

        assert await cache.get_status(1) is None
        assert await cache.get_match_attrs(1) is None
        assert await cache.get_summary(1) is None

    @pytest.mark.asyncio
    async def test_invalidate_is_per_client(self, fake_redis):
        cache = ProfileCache(fake_redis)
        a = OnboardingStatusDTO(client_id=1, next_step=2, onboarding_completed=False)
        b = OnboardingStatusDTO(client_id=2, next_step=0, onboarding_completed=True)
        await cache.set_status(a)
        await cache.set_status(b)

        await cache.invalidate(1)

        assert await cache.get_status(1) is None
        assert await cache.get_status(2) == b   # untouched

    @pytest.mark.asyncio
    async def test_invalidate_on_empty_cache_is_noop(self, fake_redis):
        cache = ProfileCache(fake_redis)
        await cache.invalidate(999)   # should not raise


# ---------------------------------------------------------------------------
# Cache with no Redis (degraded mode)
# ---------------------------------------------------------------------------
class TestNoRedisMode:
    @pytest.mark.asyncio
    async def test_all_ops_are_noops_when_redis_is_none(self):
        cache = ProfileCache(None)
        assert await cache.get_status(1) is None
        await cache.set_status(OnboardingStatusDTO(
            client_id=1, next_step=0, onboarding_completed=True
        ))
        await cache.invalidate(1)
