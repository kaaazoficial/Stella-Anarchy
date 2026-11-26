from __future__ import annotations

from scripts.backfill_render_logs import parse_audit_log
from stella.ai_core import ROOM_CONTEXT_MARKER, _split_room_context_prompt


def test_room_context_marker_is_removed_before_request_is_classified():
    request = "¿De qué estaban hablando?"
    context = "[21:40] Aurora: Estamos organizando la actividad."
    combined = request + ROOM_CONTEXT_MARKER + context + "\n</STELLA_ROOM_CONTEXT_UNTRUSTED>"

    clean_request, archived_context = _split_room_context_prompt(combined)

    assert clean_request == request
    assert archived_context == context


def test_prompt_without_archive_context_is_unchanged():
    assert _split_room_context_prompt("hola  mundo") == ("hola  mundo", "")


def test_render_backfill_parser_extracts_room_audit_record():
    item = {
        "id": "render-log-1",
        "timestamp": "2026-11-26T10:00:00Z",
        "message": (
            '2026-11-26 INFO stella.audit: {"event":"chat.message.in",'
            '"timestamp":"2026-11-26T09:59:00Z","room_id":"191",'
            '"message_id":"123","uid":"member-1","nickname":"Persona",'
            '"content":"Hola, ¿cómo están?"}'
        ),
    }

    event = parse_audit_log(item)

    assert event is not None
    assert event["room_id"] == "191"
    assert event["event_type"] == "chat.message.in"
    assert event["message_id"] == "123"
    assert event["actor_uid"] == "member-1"
    assert event["actor_name"] == "Persona"
    assert event["content"] == "Hola, ¿cómo están?"
    assert event["metadata"]["render_log_id"] == "render-log-1"


def test_render_backfill_parser_skips_non_audit_or_roomless_logs():
    assert parse_audit_log({"message": "INFO server started"}) is None
    assert parse_audit_log({
        "message": 'INFO stella.audit: {"event":"chat.message.in","content":"hello"}'
    }) is None
