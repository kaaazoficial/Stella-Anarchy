import json
import logging

from stella.audit import audit_event, redact_secrets


def test_audit_event_logs_full_chat_message_and_context(caplog):
    with caplog.at_level(logging.INFO, logger="stella.audit"):
        audit_event(
            "chat.message.in",
            room_id="room-1",
            uid="user-1",
            nickname="Persona",
            message_id="message-1",
            content="Hola Stella, ¿cómo estás?",
            addressed=False,
        )

    records = [record.message for record in caplog.records if record.name == "stella.audit"]
    payload = json.loads(records[-1])
    assert payload["event"] == "chat.message.in"
    assert payload["room_id"] == "room-1"
    assert payload["uid"] == "user-1"
    assert payload["message_id"] == "message-1"
    assert payload["nickname"] == "Persona"
    assert payload["content"] == "Hola Stella, ¿cómo estás?"
    assert payload["content_length"] == len("Hola Stella, ¿cómo estás?")
    assert payload["content_redacted"] is False
    assert payload["addressed"] is False
    assert payload["timestamp"]


def test_audit_event_redacts_secrets_but_keeps_the_rest_of_message(caplog):
    message = "Hola, mi api_key=secret-value y token gsk_abcdefghijklmnop123456"
    with caplog.at_level(logging.INFO, logger="stella.audit"):
        audit_event("chat.message.in", content=message)
    record = next(record for record in caplog.records if record.name == "stella.audit")
    payload = json.loads(record.message)
    assert "Hola" in payload["content"]
    assert "secret-value" not in payload["content"]
    assert "gsk_abcdefghijklmnop123456" not in payload["content"]
    assert payload["content_redacted"] is True


def test_audit_logs_redact_common_api_credentials():
    content = "mi api_key=secret-value y token gsk_abcdefghijklmnop123456"
    cleaned = redact_secrets(content)
    assert "secret-value" not in cleaned
    assert "gsk_abcdefghijklmnop123456" not in cleaned
    assert "[REDACTED]" in cleaned or "[REDACTED_SECRET]" in cleaned


def test_audit_event_keeps_multiline_content_in_one_json_record(caplog):
    with caplog.at_level(logging.INFO, logger="stella.audit"):
        audit_event("chat.message.in", content="primera línea\nsegunda línea")
    record = next(record for record in caplog.records if record.name == "stella.audit")
    payload = json.loads(record.message)
    assert payload["content"] == "primera línea\nsegunda línea"
    assert "primera línea" in record.message
    assert "segunda línea" in record.message
