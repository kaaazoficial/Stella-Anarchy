import json
import logging

from stella.audit import audit_event, redact_secrets


def _last_record(caplog):
    records = [record.message for record in caplog.records if record.name == "stella.audit"]
    assert records
    return records[-1]


def test_chat_log_is_readable_and_hides_identifiers_by_default(caplog, monkeypatch):
    monkeypatch.delenv("STELLA_AUDIT_LOG_CONTENT", raising=False)
    with caplog.at_level(logging.INFO, logger="stella.audit"):
        audit_event(
            "chat.message.in",
            room_id="room-1",
            uid="user-1",
            nickname="Nombre visible",
            actor_uid="actor-1",
            actor_name="Nombre visible",
            message_id="message-1",
            content="Hola Stella, ¿cómo estás?",
            mentions=[{"uid": "mentioned-user"}],
            reply_message_id="parent-message",
        )

    record = _last_record(caplog)
    assert record.startswith("[CHAT] ")
    assert "Sala room-1" in record
    assert "Usuario ↪: [contenido omitido]" in record
    assert "Nombre visible" not in record
    assert "Hola Stella" not in record
    assert "actor-1" not in record
    assert "message-1" not in record
    assert "content_length" not in record
    assert record.startswith("[CHAT] ")


def test_chat_log_is_concise_and_redacts_sensitive_content(caplog, monkeypatch):
    monkeypatch.setenv("STELLA_AUDIT_LOG_CONTENT", "true")
    message = (
        "Hola, mi api_key=secret-value, token gsk_abcdefghijklmnop123456, "
        "correo persona@example.com y teléfono +56912345678"
    )
    with caplog.at_level(logging.INFO, logger="stella.audit"):
        audit_event(
            "chat.message.in",
            room_id="room-191",
            room_name="General",
            message_id="message-456",
            actor_uid="member-123",
            actor_name="Jean",
            reply_to_message_id="parent-455",
            event_time="2026-11-28T09:00:00Z",
            content=message,
        )

    record = _last_record(caplog)
    assert record.startswith("[CHAT] 2026-11-28 06:00:00 | General (room-191) | Jean ↪: ")
    assert "Hola" in record
    assert "secret-value" not in record
    assert "gsk_abcdefghijklmnop123456" not in record
    assert "persona@example.com" not in record
    assert "+56912345678" not in record
    assert "message-456" not in record
    assert "member-123" not in record
    assert "content_length" not in record
    assert "content_logged" not in record


def test_audit_logs_redact_common_api_credentials_and_contact_details():
    content = (
        "mi api_key=secret-value y token gsk_abcdefghijklmnop123456 "
        "correo persona@example.com teléfono +56912345678"
    )
    cleaned = redact_secrets(content)
    assert "secret-value" not in cleaned
    assert "gsk_abcdefghijklmnop123456" not in cleaned
    assert "persona@example.com" not in cleaned
    assert "+56912345678" not in cleaned
    assert "[REDACTED]" in cleaned or "[REDACTED_SECRET]" in cleaned
    assert "[EMAIL_OMITIDO]" in cleaned
    assert "[TELEFONO_OMITIDO]" in cleaned


def test_multiline_chat_is_kept_on_one_readable_log_line(caplog, monkeypatch):
    monkeypatch.setenv("STELLA_AUDIT_LOG_CONTENT", "1")
    with caplog.at_level(logging.INFO, logger="stella.audit"):
        audit_event("chat.message.in", content="primera línea\nsegunda línea")

    record = _last_record(caplog)
    assert "primera línea ↵ segunda línea" in record
    assert "\n" not in record


def test_long_chat_content_is_capped(caplog, monkeypatch):
    monkeypatch.setenv("STELLA_AUDIT_LOG_CONTENT", "1")
    with caplog.at_level(logging.INFO, logger="stella.audit"):
        audit_event("chat.message.in", content="x" * 2500)

    record = _last_record(caplog)
    assert len(record.split(": ", 1)[1].removesuffix(" … [recortado]")) == 2000
    assert record.endswith(" … [recortado]")


def test_non_chat_audit_events_keep_structured_metadata(caplog):
    with caplog.at_level(logging.INFO, logger="stella.audit"):
        audit_event("admin.command", command="diagnostico", authorized=True)

    payload = json.loads(_last_record(caplog))
    assert payload["event"] == "admin.command"
    assert payload["command"] == "diagnostico"
    assert payload["authorized"] is True
