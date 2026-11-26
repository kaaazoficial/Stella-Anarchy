from __future__ import annotations

import hashlib
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


def _audit_fingerprint(value: Any) -> str:
    """Create a short, non-reversible correlation token for audit metadata."""
    raw = str(value or "").encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()[:12]


def audit_event(event: str, **fields: Any) -> None:
    """Emit metadata-only audit events; conversation text never enters logs."""
    payload = {
        "event": str(event),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    for key, value in fields.items():
        if value is None:
            continue
        field = str(key)
        if field in {"content", "text"}:
            payload["content_length"] = len(str(value))
            payload["content_redacted"] = True
        elif field in {"uid", "message_id", "reply_id", "room_id"}:
            payload[f"{field}_hash"] = _audit_fingerprint(value)
        elif field == "nickname":
            # Nicknames are user-controlled and may contain personal information.
            continue
        else:
            payload[field] = value
    log.info("%s", json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str))
