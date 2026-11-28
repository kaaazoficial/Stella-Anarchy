from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from stella.ai_core import ROOM_CONTEXT_MARKER, REFERENCE_CONTEXT_MARKER
from stella.bot import StellaBot


def test_global_chat_silence_cache_avoids_a_remote_read_for_every_message(monkeypatch):
    now = [100.0]

    class SharedState:
        def __init__(self):
            self.value = "0"
            self.calls = 0

        async def get_setting(self, key):
            self.calls += 1
            if key == "chat_silence_until":
                import time
                return str(int(time.time()) + 600)
            assert key == "chat_silence"
            return self.value

    bot = object.__new__(StellaBot)
    bot.shared_state = SharedState()
    bot._global_chat_silence_cache = None
    monkeypatch.setattr("stella.bot.time.monotonic", lambda: now[0])

    async def scenario():
        assert await bot._global_chat_silenced() is False
        assert await bot._global_chat_silenced() is False
        assert bot.shared_state.calls == 1

        # A change from another Render instance becomes visible at cache expiry.
        bot.shared_state.value = "1"
        assert await bot._global_chat_silenced() is False
        assert bot.shared_state.calls == 1

        now[0] += 1.01
        assert await bot._global_chat_silenced() is True
        assert bot.shared_state.calls == 3

    asyncio.run(scenario())


def test_global_chat_silence_fails_closed_and_caches_the_failure(monkeypatch):
    now = [200.0]

    class SharedState:
        calls = 0

        async def get_setting(self, key):
            self.calls += 1
            raise RuntimeError("Qdrant unavailable")

    bot = object.__new__(StellaBot)
    bot.shared_state = SharedState()
    bot._global_chat_silence_cache = None
    monkeypatch.setattr("stella.bot.time.monotonic", lambda: now[0])

    async def scenario():
        assert await bot._global_chat_silenced() is True
        assert await bot._global_chat_silenced() is True
        assert bot.shared_state.calls == 1

    asyncio.run(scenario())


def test_room_wide_invitation_can_trigger_stella_without_presence_command():
    bot = object.__new__(StellaBot)
    bot._proactive_cooldown = {}
    bot.consciousness = SimpleNamespace(
        should_initiate=lambda text: True,
        context=lambda room: "Estado interno simulado",
    )
    bot.ai = SimpleNamespace(ask=AsyncMock(return_value="Yo probaría con una película de misterio."))
    bot._room_history_prompt = AsyncMock(side_effect=lambda room, uid, prompt, message: prompt)
    bot._reply = AsyncMock()

    async def scenario():
        await bot._maybe_proactive_reply(
            "room-1",
            "member-1",
            "Participante",
            "¿Alguien conoce alguna buena película de misterio?",
            {"messageId": "m-1"},
        )
        assert bot.ai.ask.await_count == 1
        bot._reply.assert_awaited_once()
        assert bot.ai.ask.await_args.kwargs["system"]

        # The room cooldown suppresses a second unsolicited answer.
        await bot._maybe_proactive_reply(
            "room-1",
            "member-2",
            "Otra persona",
            "¿Alguien conoce otra película?",
            {"messageId": "m-2"},
        )
        assert bot.ai.ask.await_count == 1
        assert bot._reply.await_count == 1

    asyncio.run(scenario())


def test_playful_outage_fallback_saves_only_the_original_user_message():
    bot = object.__new__(StellaBot)
    captured_turns = []

    class Core:
        async def generate_playful_response(self, *args, **kwargs):
            raise RuntimeError("provider unavailable")

        def _append_history(self, session_key, role, content):
            captured_turns.append((role, content))

    bot.ai_core = Core()
    bot.services = SimpleNamespace(ai_request=object())
    bot.consciousness = SimpleNamespace(context=lambda room: "INTERNAL_CONTEXT")
    bot._load_ai_context = AsyncMock(return_value=[])
    bot._room_history_prompt = AsyncMock(
        side_effect=lambda room, uid, prompt, message: (
            prompt + ROOM_CONTEXT_MARKER + "ROOM_HISTORY"
        )
    )
    bot._reply = AsyncMock()
    bot._schedule_background = lambda coroutine, label: coroutine.close()

    async def scenario():
        await bot._ai_assist(
            "room-2",
            "member-9",
            "Participante",
            "Stella, eres una idiota",
            {"messageId": "m-9"},
            playful_kind="insult",
        )

    asyncio.run(scenario())

    user_turns = [content for role, content in captured_turns if role == "user"]
    assert user_turns == ["Stella, eres una idiota"]
    assert all(ROOM_CONTEXT_MARKER not in item for item in user_turns)
    assert all(REFERENCE_CONTEXT_MARKER not in item for item in user_turns)
    bot._reply.assert_awaited_once()


def test_memory_save_does_not_read_the_opt_in_flag_twice():
    class Memory:
        def __init__(self):
            self.saved = None

        async def is_enabled(self, *_args):
            raise AssertionError("save() rechecks the durable opt-out tombstone")

        async def save(self, room_id, uid, turns):
            self.saved = (room_id, uid, list(turns))
            return True

    class Store:
        def get_setting(self, room_id, key, default="0"):
            return "0"

    class Core:
        def _history(self, key):
            return [{"role": "user", "content": "Seguimos con la conversación."}]

    bot = object.__new__(StellaBot)
    bot.ai_memory = Memory()
    bot.store = Store()
    bot.ai_core = Core()

    result = asyncio.run(bot._save_ai_memory("room", "member", "room:member"))

    assert result is True
    assert bot.ai_memory.saved == (
        "room",
        "member",
        [{"role": "user", "content": "Seguimos con la conversación."}],
    )
