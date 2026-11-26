from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

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
)


def redact_secrets(value: Any) -> str:
    """Keep chat text searchable while masking common credentials before logging."""
    text = str(value or "")
    for pattern, replacement in _SECRET_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


def audit_event(event: str, **fields: Any) -> None:
    """Write structured chat/audit events to stdout so Render captures them."""
    payload: dict[str, Any] = {
        "event": str(event),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    never_log = {
        "authorization", "headers", "api_key", "gemini_api_key",
        "google_api_key", "groq_api_key", "anarchy_bot_token",
        "anarchy_webhook_secret", "qdrant_api_key", "webhook_secret",
        "token", "secret", "password",
    }
    for key, value in fields.items():
        if value is None:
            continue
        field = str(key)
        if field.casefold() in never_log:
            continue
        if field in {"content", "text"}:
            text = str(value or "")
            safe_text = redact_secrets(text)
            payload["content"] = safe_text
            payload["content_length"] = len(text)
            payload["content_redacted"] = safe_text != text
        elif isinstance(value, str):
            payload[field] = redact_secrets(value)
        else:
            payload[field] = value
    log.info("%s", json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str))
