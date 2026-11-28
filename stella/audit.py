from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger("stella.audit")

_SECRET_REPLACEMENTS = (
    (
        re.compile(r"(?i)\bauthorization\s*[:=]\s*(?:bot|bearer)\s+[^\s,;]+"),
        "Authorization=[REDACTED]",
    ),
    (
        re.compile(r"(?i)\b(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|contraseña|authorization|client_secret|webhook_secret)\s*[:=]\s*([^\s,;]+)"),
        r"\1=[REDACTED]",
    ),
    (
        re.compile(r"(?i)([?&](?:access_token|api_key|token|secret|password)=)[^&#\s]+"),
        r"\1[REDACTED]",
    ),
    (
        re.compile(
            r"\b(?:anb_[A-Za-z0-9_-]{12,}|gsk_[A-Za-z0-9_-]{12,}|"
            r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
            r"xox[baprs]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9_-]{16,}|"
            r"AQ\.[A-Za-z0-9_-]{20,})\b"
        ),
        "[REDACTED_SECRET]",
    ),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        "[REDACTED_SECRET]",
    ),
    (
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (
        re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
        "[EMAIL_OMITIDO]",
    ),
    (
        re.compile(r"(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w)"),
        "[TELEFONO_OMITIDO]",
    ),
)

_PRIVATE_FIELDS = frozenset({
    "uid", "user_id", "userid", "author_id", "authorid",
    "nickname", "display_name", "displayname",
    "message_id", "messageid", "event_id", "eventid",
    "actor_uid", "actor_name",
})

_MAX_AUDIT_CONTENT_CHARS = 2000


def _local_event_time(value: Any) -> tuple[str | None, str | None]:
    """Return a local-time rendering and timezone name for an event timestamp."""
    zone_name = os.getenv("STELLA_AUDIT_TIMEZONE", "America/Santiago").strip() or "UTC"
    try:
        zone = ZoneInfo(zone_name)
    except Exception:
        zone_name = "UTC"
        zone = timezone.utc

    try:
        if isinstance(value, (int, float)):
            stamp = float(value)
            if abs(stamp) > 10_000_000_000:
                stamp /= 1000.0
            parsed = datetime.fromtimestamp(stamp, tz=timezone.utc)
        else:
            raw = str(value or "").strip()
            if not raw:
                return None, None
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(zone).isoformat(), zone_name
    except (TypeError, ValueError, OverflowError, OSError):
        return None, None


def redact_secrets(value: Any) -> str:
    """Mask credentials and direct contact details before optional audit logging."""
    text = str(value or "")
    for pattern, replacement in _SECRET_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


def _content_logging_enabled() -> bool:
    """Raw chat text is excluded from logs unless an operator explicitly opts in."""
    return os.getenv("STELLA_AUDIT_LOG_CONTENT", "").strip().casefold() in {
        "1", "true", "yes", "on",
    }


def audit_event(event: str, **fields: Any) -> None:
    """Write structured operational events without retaining raw chat or member identifiers."""
    payload: dict[str, Any] = {
        "event": str(event),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    received_local, received_zone = _local_event_time(payload["timestamp"])
    if received_local:
        payload["timestamp_local"] = received_local
        payload["timestamp_timezone"] = received_zone
    never_log = {
        "authorization", "headers", "api_key", "gemini_api_key",
        "google_api_key", "groq_api_key", "anarchy_bot_token",
        "anarchy_webhook_secret", "qdrant_api_key", "webhook_secret",
        "token", "secret", "password",
    }
    keep_content = _content_logging_enabled()
    for key, value in fields.items():
        if value is None:
            continue
        field = str(key)
        normalized = field.casefold()
        if normalized in never_log:
            continue
        if normalized in {"actor_uid", "actor_name", "message_id", "messageid"}:
            if keep_content:
                output_key = "message_id" if normalized == "messageid" else field
                max_length = 120 if normalized == "actor_name" else 160
                payload[output_key] = redact_secrets(value)[:max_length]
            elif normalized in {"message_id", "messageid"}:
                payload["message_reference_present"] = bool(value)
            continue
        if normalized in _PRIVATE_FIELDS:
            continue
        if normalized in {"content", "text"}:
            raw_text = str(value or "")
            payload["content_length"] = len(raw_text)
            payload["content_logged"] = keep_content
            if keep_content:
                safe_text = redact_secrets(raw_text)
                payload["content"] = safe_text[:_MAX_AUDIT_CONTENT_CHARS]
                payload["content_truncated"] = len(safe_text) > _MAX_AUDIT_CONTENT_CHARS
                payload["content_redacted"] = safe_text != raw_text
            continue
        if normalized in {
            "reply_message_id", "replymessageid",
            "reply_to_message_id", "replytomessageid", "reply_id",
        }:
            payload["has_reply"] = bool(value)
            if keep_content:
                payload["reply_to_message_id"] = redact_secrets(value)[:160]
            continue
        if normalized == "mentions":
            try:
                payload["mention_count"] = len(value)
            except TypeError:
                payload["mention_count"] = int(bool(value))
            continue
        if normalized in {"event_time", "occurred_at", "sent_at"}:
            payload[field] = redact_secrets(value)[:100]
            local_time, local_zone = _local_event_time(value)
            if local_time:
                payload["event_time_local"] = local_time
                payload["event_timezone"] = local_zone
            continue
        if isinstance(value, str):
            payload[field] = redact_secrets(value)
        else:
            payload[field] = value
    log.info("%s", json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str))
