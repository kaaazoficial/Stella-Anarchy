from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("stella.audit")

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|contraseña|authorization)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"\b(?:gsk_[A-Za-z0-9_-]{12,}|AQ\.[A-Za-z0-9_-]{20,})\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
)


def redact_secrets(value: Any) -> str:
    """Preserve message text while preventing common credentials from entering logs."""
    text = str(value or "")
    text = _SECRET_PATTERNS[0].sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    for pattern in _SECRET_PATTERNS[1:]:
        text = pattern.sub("[REDACTED_SECRET]", text)
    return text


def audit_event(event: str, **fields: Any) -> None:
    """Emit a structured, content-bearing audit event to stdout/Render logs."""
    payload = {
        "event": str(event),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    for key, value in fields.items():
        if value is None:
            continue
        payload[str(key)] = redact_secrets(value) if key in {"content", "text", "nickname"} else value
    log.info("%s", json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str))
