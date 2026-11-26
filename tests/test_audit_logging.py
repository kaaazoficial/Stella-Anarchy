import json
import logging

from stella.audit import audit_event, redact_secrets


def test_audit_event_logs_metadata_without_message_content(caplog):
    with caplog.at_level(logging.INFO, logger="stella.audit"):
        audit_event(
            "chat.message.in",
            room_id="room-1",
            uid="user-1",
            nickname="Persona",
            message_id="message-1",
            content="Hola Stella, ¿cómo estás?",
        )

    records = [record.message for record in caplog.records if record.name == "stella.audit"]
    payload = json.loads(records[-1])
    rendered = records[-1]
    assert payload["event"] == "chat.message.in"
    assert payload["room_id_hash"]
    assert payload["uid_hash"]
    assert payload["message_id_hash"]
    assert "room-1" not in rendered
    assert "user-1" not in rendered
    assert "Persona" not in rendered
    assert "Hola Stella" not in rendered
    assert payload["content_length"] == len("Hola Stella, ¿cómo estás?")
    assert payload["content_redacted"] is True
    assert payload["timestamp"]


def test_audit_logs_redact_common_api_credentials():
    content = "mi api_key=secret-value y token gsk_abcdefghijklmnop123456"
    cleaned = redact_secrets(content)
    assert "secret-value" not in cleaned
    assert "gsk_abcdefghijklmnop123456" not in cleaned
    assert "[REDACTED]" in cleaned or "[REDACTED_SECRET]" in cleaned


def test_audit_event_keeps_multiline_content_out_of_single_json_record(caplog):
    with caplog.at_level(logging.INFO, logger="stella.audit"):
        audit_event("chat.message.in", content="primera línea\nsegunda línea")
    record = next(record for record in caplog.records if record.name == "stella.audit")
    payload = json.loads(record.message)
    assert payload["content_length"] == len("primera línea\nsegunda línea")
    assert payload["content_redacted"] is True
    assert "primera línea" not in record.message
