from datetime import datetime
from typing import List

import pytest

from app.fittbot_api.v2.Fymble.gym_mate.blocks import _domain as d
from app.fittbot_api.v2.Fymble.gym_mate.blocks._events import (
    NoopEventBus,
    UserBlocked,
    UserUnblocked,
)
from app.fittbot_api.v2.Fymble.gym_mate.blocks._service import BlockService
from app.utils.logging_utils import FittbotHTTPException


class InMemoryBlockRepository:
    def __init__(self):
        self.pairs: set[tuple[int, int]] = set()
        self.client_info: dict[int, tuple[str, str]] = {}

    async def add(self, blocker_id, blocked_id, when):
        key = (blocker_id, blocked_id)
        if key in self.pairs:
            return False
        self.pairs.add(key)
        return True

    async def remove(self, blocker_id, blocked_id):
        key = (blocker_id, blocked_id)
        if key in self.pairs:
            self.pairs.remove(key)
            return True
        return False

    async def list_blocked_by(self, blocker_id):
        rows = []
        i = 1
        for b, t in self.pairs:
            if b == blocker_id:
                info = self.client_info.get(t, (None, None))
                rows.append({
                    "block_id": i,
                    "client_id": t,
                    "name": info[0],
                    "avatar_url": info[1],
                    "blocked_at": datetime.now(),
                })
                i += 1
        return rows

    async def is_blocked_either_way(self, a, b):
        return (a, b) in self.pairs or (b, a) in self.pairs

    async def get_blocked_ids(self, blocker_id):
        return [t for (b, t) in self.pairs if b == blocker_id]

    async def get_bidirectional_block_ids(self, client_id):
        out = set()
        for a, b in self.pairs:
            if a == client_id:
                out.add(b)
            elif b == client_id:
                out.add(a)
        return out

    async def cascade_cleanup_on_block(self, blocker_id, blocked_id, now):
        # In-memory fake — real cascade lives in the SQL repo. The test
        # only needs this method to exist so the service call succeeds.
        return {
            "friendship_removed": False,
            "friend_requests_cancelled": 0,
            "session_requests_withdrawn": 0,
        }


class RecordingBus:
    def __init__(self): self.events = []
    async def publish(self, event): self.events.append(event)


@pytest.fixture
def repo(): return InMemoryBlockRepository()

@pytest.fixture
def bus(): return RecordingBus()

@pytest.fixture
def service(repo, bus):
    return BlockService(repository=repo, event_bus=bus)


class TestBlock:
    @pytest.mark.asyncio
    async def test_blocks_pair(self, service, repo):
        await service.block(blocker_id=42, blocked_id=99)
        assert (42, 99) in repo.pairs

    @pytest.mark.asyncio
    async def test_block_self_rejected(self, service):
        with pytest.raises(FittbotHTTPException) as exc:
            await service.block(blocker_id=42, blocked_id=42)
        assert exc.value.error_code == "GYMMATE_BLOCK_SELF"

    @pytest.mark.asyncio
    async def test_publishes_event_on_new_block(self, service, bus):
        await service.block(blocker_id=42, blocked_id=99)
        assert any(isinstance(e, UserBlocked) for e in bus.events)

    @pytest.mark.asyncio
    async def test_dup_block_no_extra_event(self, service, bus):
        await service.block(blocker_id=42, blocked_id=99)
        bus.events.clear()
        await service.block(blocker_id=42, blocked_id=99)
        assert bus.events == []


class TestUnblock:
    @pytest.mark.asyncio
    async def test_removes_pair(self, service, repo):
        await service.block(blocker_id=42, blocked_id=99)
        await service.unblock(blocker_id=42, blocked_id=99)
        assert (42, 99) not in repo.pairs

    @pytest.mark.asyncio
    async def test_unblock_unknown_is_noop(self, service, bus):
        await service.unblock(blocker_id=42, blocked_id=999)
        assert not any(isinstance(e, UserUnblocked) for e in bus.events)


class TestListAndChecks:
    @pytest.mark.asyncio
    async def test_list_blocked(self, service, repo):
        repo.client_info[99] = ("Anamika", "https://x/a.jpg")
        await service.block(blocker_id=42, blocked_id=99)
        result = await service.list_blocked(blocker_id=42)
        assert len(result) == 1
        assert result[0].client_id == 99
        assert result[0].name == "Anamika"
        assert result[0].avatar_url == "https://x/a.jpg"

    @pytest.mark.asyncio
    async def test_is_blocked_either_way(self, service):
        await service.block(blocker_id=42, blocked_id=99)
        assert await service.is_blocked_either_way(42, 99) is True
        # reverse direction also true
        assert await service.is_blocked_either_way(99, 42) is True

    @pytest.mark.asyncio
    async def test_get_blocked_ids(self, service):
        await service.block(blocker_id=42, blocked_id=99)
        await service.block(blocker_id=42, blocked_id=100)
        ids = await service.get_blocked_ids(42)
        assert sorted(ids) == [99, 100]


class TestOnChangeCallback:
    @pytest.mark.asyncio
    async def test_block_fires_callback(self):
        calls = []

        async def cb(client_id):
            calls.append(client_id)

        svc = BlockService(
            repository=InMemoryBlockRepository(),
            event_bus=NoopEventBus(),
            on_change=cb,
        )
        await svc.block(blocker_id=42, blocked_id=99)
        # Block fires the home-invalidator for BOTH sides — the blocked
        # user's feeds (inbox, friends, suggestions) also need to drop
        # the blocker right away.
        assert calls == [42, 99]
