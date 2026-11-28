from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from stella.bot import StellaBot
from stella.room_archive import RoomArchive


def test_antispam_does_not_load_settings_for_every_tracked_room():
    bot = object.__new__(StellaBot)
    bot._is_admin = lambda _uid: False
    bot._recent = defaultdict(deque)
    bot._warned_until = {}
    bot._auto_reply_cooldown = {}
    now = time.monotonic()
    bot._last_antispam_prune = now
    for index in range(500):
        bot._recent[(f"room-{index}", f"member-{index}")].append((now, "old-digest"))

    reads = []

    def config(room_id):
        reads.append(room_id)
        return {
            "enabled": True,
            "max_messages": 7,
            "window_seconds": 8,
            "repeat_limit": 3,
            "max_links": 4,
            "max_mentions": 8,
        }

    bot._antispam_config = config
    blocked, reason = bot._check_antispam("active-room", "active-member", "hello")

    assert (blocked, reason) == (False, None)
    assert reads == ["active-room"]
    assert len(bot._recent) == 501


def test_antispam_global_prune_is_throttled_and_uses_safe_maximum_window():
    bot = object.__new__(StellaBot)
    bot._is_admin = lambda _uid: False
    bot._recent = defaultdict(deque)
    bot._warned_until = {}
    bot._auto_reply_cooldown = {}
    now = time.monotonic()
    bot._last_antispam_prune = now - 20
    bot._recent[("old-room", "old-member")].append((now - 61, "expired"))
    bot._recent[("current-room", "current-member")].append((now - 9, "out-of-window"))

    reads = []

    def config(room_id):
        reads.append(room_id)
        return {
            "enabled": True,
            "max_messages": 7,
            "window_seconds": 8,
            "repeat_limit": 3,
            "max_links": 4,
            "max_mentions": 8,
        }

    bot._antispam_config = config
    blocked, reason = bot._check_antispam("current-room", "current-member", "hello")

    assert (blocked, reason) == (False, None)
    assert reads == ["current-room"]
    assert ("old-room", "old-member") not in bot._recent
    assert list(bot._recent[("current-room", "current-member")])[-1][1] != "out-of-window"


def test_room_context_reuses_durable_snapshot_for_rapid_messages(monkeypatch):
    archive = RoomArchive()
    now = [100.0]
    monkeypatch.setattr("stella.room_archive.time.monotonic", lambda: now[0])
    queries = []
    stored = [{
        "room_id": "room-a",
        "event_type": "chat.message.in",
        "message_id": "stored-1",
        "actor_uid": "member-1",
        "content": "Estamos preparando un evento comunitario.",
        "occurred_at_ms": 1000,
    }]

    async def fake_list_room_events(room_id, **kwargs):
        queries.append((room_id, kwargs))
        return [dict(item) for item in stored]

    archive.list_room_events = fake_list_room_events

    def add(message_id, content, offset):
        stamp = datetime.now(timezone.utc).isoformat()
        archive._remember_context_event({
            "room_id": "room-a",
            "event_type": "chat.message.in",
            "event_time": stamp,
            "message_id": message_id,
            "actor_uid": "member-2",
            "actor_name": "Participante",
            "content": content,
        })

    async def scenario():
        add("local-1", "Podríamos hacerlo este sábado.", 1)
        first = await archive.recent_room_context("room-a", limit=24)
        assert {item["message_id"] for item in first} == {"stored-1", "local-1"}
        assert len(queries) == 1

        add("local-2", "¿Qué actividades propondrían?", 2)
        second = await archive.recent_room_context("room-a", limit=24)
        assert {item["message_id"] for item in second} == {"stored-1", "local-1", "local-2"}
        assert len(queries) == 1

        now[0] += archive._recent_context_snapshot_ttl_seconds + 0.1
        third = await archive.recent_room_context("room-a", limit=24)
        assert {item["message_id"] for item in third} == {"stored-1", "local-1", "local-2"}
        assert len(queries) == 2

    asyncio.run(scenario())
