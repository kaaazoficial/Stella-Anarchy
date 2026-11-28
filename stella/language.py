"""Small language helpers for Stella's user-facing messages."""

from __future__ import annotations

import re


def split_arguments(value: str, expected: int | None = None) -> list[str]:
    parts = [part.strip() for part in str(value or "").split("|")]
    if expected is not None and len(parts) < expected:
        return []
    return parts


def safe_display_name(value: str) -> str:
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", str(value or "Usuario"))
    return " ".join(cleaned.split())[:80] or "Usuario"
