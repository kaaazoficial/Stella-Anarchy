from __future__ import annotations

from stella.conversation_context import is_clear_invitation, select_conversation_events


NOW = 1_800_000_000_000


def event(message_id, content, *, actor="u1", reply_to="", minutes_ago=1):
    return {
        "message_id": message_id,
        "actor_uid": actor,
        "actor_name": actor,
        "reply_to_message_id": reply_to,
        "content": content,
        "occurred_at_ms": NOW - minutes_ago * 60_000,
    }


def test_reply_context_follows_parent_chain_and_ignores_other_topics():
    events = [
        event("noise", "¿Alguien ha probado este videojuego?", actor="u3"),
        event("child", "*Stella observa la puerta.*", actor="u2", reply_to="root"),
        event("root", "*El personaje entra en la biblioteca.*", actor="u1", minutes_ago=4),
        event("unrelated", "La receta de pizza quedó genial", actor="u4", minutes_ago=5),
    ]
    selected = select_conversation_events(
        events,
        query="*Stella observa la puerta.*",
        reply_to_message_id="root",
        current_message_id="child",
        actor_uid="u2",
        now_ms=NOW,
    )
    assert [item["message_id"] for item in selected] == ["root"]


def test_topic_selection_avoids_unrelated_recent_messages():
    events = [
        event("a", "La base de datos PostgreSQL sirve para proyectos medianos", minutes_ago=1),
        event("b", "Hoy jugué Minecraft con mis amigos", actor="u2", minutes_ago=2),
        event("c", "Para PostgreSQL revisaría primero el tamaño de los datos", actor="u3", minutes_ago=3),
        event("d", "Qué bonito quedó el dibujo", actor="u4", minutes_ago=4),
    ]
    selected = select_conversation_events(
        events,
        query="¿Qué ventajas tiene PostgreSQL para mi proyecto?",
        actor_uid="u1",
        now_ms=NOW,
    )
    ids = {item["message_id"] for item in selected}
    assert {"a", "c"}.issubset(ids)
    assert "b" not in ids
    assert "d" not in ids


def test_short_follow_up_uses_recent_room_sequence():
    events = [
        event("last", "Entonces podríamos probar con la segunda alternativa", minutes_ago=1),
        event("middle", "La primera alternativa consume demasiados recursos", actor="u2", minutes_ago=2),
        event("first", "Tenemos dos opciones para el servidor", minutes_ago=3),
        event("old", "La semana pasada hablamos de otra cosa", minutes_ago=200),
    ]
    selected = select_conversation_events(
        events, query="¿Y por qué?", actor_uid="u1", now_ms=NOW
    )
    ids = {item["message_id"] for item in selected}
    assert {"last", "middle", "first"}.issubset(ids)
    assert "old" not in ids


def test_roleplay_continuation_preserves_scene_and_excludes_other_chat():
    events = [
        event("rp3", "—No voy a entrar todavía.", actor="u2", minutes_ago=1),
        event("rp2", "*Stella se detiene frente a la puerta.*", minutes_ago=2),
        event("chat", "¿Alguien sabe la hora?", actor="u3", minutes_ago=3),
        event("rp1", "*La biblioteca está en silencio.*", actor="u1", minutes_ago=4),
    ]
    selected = select_conversation_events(
        events, query="continúa", actor_uid="u1", now_ms=NOW
    )
    ids = [item["message_id"] for item in selected]
    assert ids == ["rp1", "rp2", "rp3"]


def test_natural_group_openings_include_opinions_and_choices():
    assert is_clear_invitation("¿Qué les parece esta propuesta para la comunidad?")
    assert is_clear_invitation("¿Con cuál se quedan, la primera o la segunda?")
    assert is_clear_invitation("Necesito algunas ideas para organizar la actividad.")
    assert is_clear_invitation("Tengo una duda sobre cómo organizar el evento.")


def test_natural_opening_detector_does_not_trigger_on_every_question_or_statement():
    assert not is_clear_invitation("¿A qué hora empieza la película?")
    assert not is_clear_invitation("Hoy jugué Minecraft con mis amigos.")
    assert not is_clear_invitation("¿Dónde está la estación de tren?")
    assert not is_clear_invitation("!menu")


def test_natural_opening_detector_handles_collective_opinions():
    assert is_clear_invitation("¿Qué opinan de cambiar el sistema de roles?")
    assert is_clear_invitation("¿Cuál elegirían para el próximo evento?")
    assert is_clear_invitation("Busco recomendaciones de juegos cooperativos.")
