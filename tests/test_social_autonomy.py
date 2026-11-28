from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from stella.bot import StellaBot


class FakeArchive:
    def __init__(self, events):
        self.events = events
        self.calls = 0

    async def recent_room_context(self, room_id, *, limit=24):
        self.calls += 1
        return list(self.events[:limit])


class FakeAI:
    def __init__(self, answer, on_call=None):
        self.answer = answer
        self.on_call = on_call
        self.calls = []

    async def ask(self, prompt, *, system=None):
        self.calls.append((prompt, system))
        if self.on_call:
            self.on_call()
        return self.answer


class FakePlatform:
    def __init__(self):
        self.identity = {"uid": "stella-account"}
        self.sent = []

    async def send_message(self, room_id, content, reply_id=None):
        self.sent.append((room_id, content, reply_id))
        return True


def make_bot(events, answer="[NO_INTERVENIR]", on_call=None):
    bot = object.__new__(StellaBot)
    bot.platform = FakePlatform()
    bot.room_archive = FakeArchive(events)
    bot.ai = FakeAI(answer, on_call=on_call)
    bot.consciousness = None
    bot._social_room_activity = {
        "room-1": {
            "last_user_activity": time.monotonic() - 180,
            "member_uids": {"member-1", "member-2"},
            "last_evaluated_at": 0.0,
            "last_spoke_at": 0.0,
        }
    }
    bot._social_autonomy_global_last_sent = 0.0
    bot._proactive_cooldown = {}
    async def not_silenced():
        return False
    bot._global_chat_silenced = not_silenced
    return bot


def recent_events():
    stamp = int(time.time() * 1000)
    return [
        {
            "event_type": "chat.message.in",
            "message_id": "message-2",
            "actor_uid": "member-2",
            "actor_name": "Alex",
            "content": "Hace tiempo que no organizamos una actividad en la comunidad.",
            "occurred_at_ms": stamp - 30_000,
        },
        {
            "event_type": "chat.message.in",
            "message_id": "message-1",
            "actor_uid": "member-1",
            "actor_name": "Sam",
            "content": "Podríamos hacer algo este fin de semana.",
            "occurred_at_ms": stamp - 60_000,
        },
    ]


def test_autonomous_social_step_starts_a_new_room_message_without_a_reply():
    async def scenario():
        bot = make_bot(recent_events(), "¿Y si organizamos una noche de juegos este fin de semana?")
        sent = await bot.autonomous_social_step()

        assert sent is True
        assert len(bot.ai.calls) == 1
        prompt, system = bot.ai.calls[0]
        assert "Podríamos hacer algo este fin de semana." in prompt
        assert "INICIATIVA SOCIAL AUTÓNOMA" in system
        assert bot.platform.sent
        room_id, content, reply_id = bot.platform.sent[0]
        assert room_id == "room-1"
        assert "noche de juegos" in content.casefold()
        assert reply_id is None
        assert bot._social_autonomy_global_last_sent > 0
        assert bot._social_room_activity["room-1"]["last_spoke_at"] > 0

    asyncio.run(scenario())


def test_autonomous_social_step_respects_the_models_decision_to_stay_silent():
    async def scenario():
        bot = make_bot(recent_events(), "[NO_INTERVENIR]")
        sent = await bot.autonomous_social_step()

        assert sent is False
        assert len(bot.ai.calls) == 1
        assert bot.platform.sent == []
        assert bot._social_autonomy_global_last_sent == 0.0

    asyncio.run(scenario())


def test_autonomous_social_step_does_not_interrupt_a_conversation_that_is_still_active():
    async def scenario():
        bot = make_bot(recent_events())
        bot._social_room_activity["room-1"]["last_user_activity"] = time.monotonic() - 25

        sent = await bot.autonomous_social_step()

        assert sent is False
        assert bot.ai.calls == []
        assert bot.platform.sent == []

    asyncio.run(scenario())


def test_autonomous_social_step_discards_a_prepared_message_if_people_resume_chat():
    async def scenario():
        bot = None

        def resume_chat():
            bot._social_room_activity["room-1"]["last_user_activity"] = time.monotonic() + 1

        bot = make_bot(recent_events(), "Podríamos retomar el tema de la actividad.", on_call=resume_chat)
        sent = await bot.autonomous_social_step()

        assert sent is False
        assert len(bot.ai.calls) == 1
        assert bot.platform.sent == []

    asyncio.run(scenario())


def test_autonomous_social_step_discards_a_prepared_message_if_room_closes():
    async def scenario():
        bot = None

        def close_room():
            bot._social_room_activity.pop("room-1", None)

        bot = make_bot(recent_events(), "Abramos un tema nuevo para el grupo.", on_call=close_room)
        sent = await bot.autonomous_social_step()

        assert sent is False
        assert bot.platform.sent == []

    asyncio.run(scenario())


def test_autonomous_social_step_honors_the_global_chat_silence_control():
    async def scenario():
        bot = make_bot(recent_events(), "¿Qué actividad les gustaría organizar?")
        async def silenced():
            return True
        bot._global_chat_silenced = silenced

        sent = await bot.autonomous_social_step()

        assert sent is False
        assert bot.ai.calls == []
        assert bot.platform.sent == []

    asyncio.run(scenario())


def test_autonomous_social_step_enforces_global_and_room_cooldowns():
    async def scenario():
        bot = make_bot(recent_events(), "¿Qué hacemos este fin de semana?")
        bot._social_autonomy_global_last_sent = time.monotonic()
        sent = await bot.autonomous_social_step()
        assert sent is False
        assert bot.ai.calls == []

        bot._social_autonomy_global_last_sent = 0.0
        bot._proactive_cooldown["room-1"] = time.monotonic() + 300
        sent = await bot.autonomous_social_step()
        assert sent is False
        assert bot.ai.calls == []

    asyncio.run(scenario())


def test_social_room_activity_tracking_keeps_only_bounded_transient_member_ids():
    bot = object.__new__(StellaBot)
    bot._social_room_activity = {}

    bot._track_social_room_activity("room-1", "member-1")
    first = bot._social_room_activity["room-1"]
    assert first["member_uids"] == {"member-1"}
    assert "content" not in first

    bot._track_social_room_activity("room-1", "member-2")
    assert bot._social_room_activity["room-1"]["member_uids"] == {"member-1", "member-2"}
