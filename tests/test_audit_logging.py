import json
import logging

from stella.audit import audit_event, redact_secrets


def _last_audit_payload(caplog):
    records = [record.message for record in caplog.records if record.name == "stella.audit"]
    assert records
    return json.loads(records[-1])


def test_audit_event_omits_raw_chat_and_member_identifiers_by_default(caplog, monkeypatch):
    monkeypatch.delenv("STELLA_AUDIT_LOG_CONTENT", raising=False)
    message = "Hola Stella, ¿cómo estás?"
    with caplog.at_level(logging.INFO, logger="stella.audit"):
        audit_event(
            "chat.message.in",
            room_id="room-1",
            uid="user-1",
            nickname="Nombre visible",
            message_id="message-1",
            content=message,
            mentions=[{"uid": "mentioned-user"}],
            reply_message_id="parent-message",
            addressed=False,
        )

    payload = _last_audit_payload(caplog)
    assert payload["event"] == "chat.message.in"
    assert payload["room_id"] == "room-1"
    assert "uid" not in payload
    assert "nickname" not in payload
    assert "message_id" not in payload
    assert "content" not in payload
    assert payload["content_length"] == len(message)
    assert payload["content_logged"] is False
    assert payload["mention_count"] == 1
    assert payload["has_reply"] is True
    assert payload["message_reference_present"] is True
    assert payload["addressed"] is False
    assert payload["timestamp"]


def test_audit_event_allows_explicit_opt_in_with_redaction(caplog, monkeypatch):
    monkeypatch.setenv("STELLA_AUDIT_LOG_CONTENT", "true")
    message = (
        "Hola, mi api_key=secret-value, token gsk_abcdefghijklmnop123456, "
        "correo persona@example.com y teléfono +56912345678"
    )
    with caplog.at_level(logging.INFO, logger="stella.audit"):
        audit_event("chat.message.in", content=message)

    payload = _last_audit_payload(caplog)
    assert "Hola" in payload["content"]
    assert "secret-value" not in payload["content"]
    assert "gsk_abcdefghijklmnop123456" not in payload["content"]
    assert "persona@example.com" not in payload["content"]
    assert "+56912345678" not in payload["content"]
    assert payload["content_logged"] is True
    assert payload["content_redacted"] is True


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


def test_audit_event_keeps_multiline_opt_in_content_in_one_json_record(caplog, monkeypatch):
    monkeypatch.setenv("STELLA_AUDIT_LOG_CONTENT", "1")
    message = "primera línea\nsegunda línea"
    with caplog.at_level(logging.INFO, logger="stella.audit"):
        audit_event("chat.message.in", content=message)
    payload = _last_audit_payload(caplog)
    record = next(record.message for record in caplog.records if record.name == "stella.audit")
    assert payload["content"] == message
    assert "primera línea" in record
    assert "segunda línea" in record
