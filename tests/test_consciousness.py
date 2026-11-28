from __future__ import annotations

import asyncio
import json

from stella.consciousness import StellaConsciousness


class FakeSharedState:
    def __init__(self):
        self.values = {}

    async def get_setting(self, key):
        return self.values.get(key)

    async def set_setting(self, key, value):
        self.values[key] = value


def test_perception_tracks_room_without_retaining_raw_text_or_member_identity():
    mind = StellaConsciousness()
    mind.observe_message(
        "room-1", "private-member-uid",
        "Estoy creando un videojuego y necesito una sugerencia.",
        addressed=True,
    )
    snapshot = mind.snapshot("room-1")
    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert snapshot["messages_seen"] == 1
    assert snapshot["addressed_messages"] == 1
    assert snapshot["last_topic"] == "juegos"
    assert "private-member-uid" not in serialized
    assert "videojuego y necesito" not in serialized


def test_room_events_are_perceived_without_retaining_identity():
    mind = StellaConsciousness()
    mind.observe_room_event("room-1", "room.join", "private-member-uid")
    snapshot = mind.snapshot("room-1")
    assert snapshot["room_activity"]["joins"] == 1
    assert "private-member-uid" not in json.dumps(snapshot)


def test_reflection_creates_bounded_autobiographical_episode():
    mind = StellaConsciousness()
    mind.observe_message("room-1", "user-1", "¿Qué opinas de esta idea?", addressed=True)
    state = mind.reflect_response(
        "room-1", "¿Qué opinas de esta idea?", "Creo que tiene potencial.",
        mode="conversacion",
    )
    assert state["responses"] == 1
    assert state["last_reflection"]
    assert state["episodes"][-1]["action"].startswith("respuesta:")
    assert "Creo que tiene potencial" not in json.dumps(state)


def test_internal_context_describes_simulated_state_and_goals():
    mind = StellaConsciousness()
    context = mind.context("room-1")
    assert "estado computacional" in context
    assert "no sentimientos reales" in context
    assert "mantener continuidad" in context
    assert "identidad estable" in context.casefold()


def test_initiative_requires_a_clear_invitation():
    mind = StellaConsciousness()
    assert mind.should_initiate("¿Alguien sabe cómo solucionar este error?")
    assert mind.should_initiate("¿Qué opinan de esta propuesta?")
    assert not mind.should_initiate("Hoy estuve jugando toda la tarde.")
    assert not mind.should_initiate("!ayuda")


def test_persistence_restores_room_autobiography_without_private_data():
    mind = StellaConsciousness()
    shared = FakeSharedState()
    mind.observe_message("room-1", "user-secret", "Estamos hablando de roleplay.", addressed=True)
    mind.reflect_response("room-1", "Estamos hablando de roleplay.", "Podemos continuar la escena.")

    async def scenario():
        assert await mind.persist_room(shared, "room-1")
        restored = StellaConsciousness()
        assert await restored.restore_room(shared, "room-1")
        snapshot = restored.snapshot("room-1")
        assert snapshot["messages_seen"] == 1
        assert snapshot["responses"] == 1
        assert snapshot["episodes"]
        saved = shared.values["stella_consciousness:v1:room:room-1"]
        assert "user-secret" not in saved
        assert "continuar la escena" not in saved

    asyncio.run(scenario())


def test_restore_is_requested_once_per_room_until_finished():
    mind = StellaConsciousness()
    assert mind.request_restore("room-1") is True
    assert mind.request_restore("room-1") is False

    async def scenario():
        await mind.restore_room(FakeSharedState(), "room-1")
        assert mind.request_restore("room-1") is False

    asyncio.run(scenario())
