"""General topic normalization helpers for Stella."""
from __future__ import annotations

import re
import unicodedata


def normalize_topic_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\\s+", " ", text).strip()


def is_domain_specific_related_request(value: str) -> bool:
    """Stella has no game-specific knowledge domain."""
    return False
