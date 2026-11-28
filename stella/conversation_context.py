"""Select relevant, room-scoped conversational context from Stella's room archive."""
from __future__ import annotations

import re
import time
import unicodedata
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9áéíóúüñ]{3,}", re.IGNORECASE)
_STOPWORDS = frozenset({
    "que", "qué", "como", "cómo", "cuando", "cuándo", "donde", "dónde",
    "para", "porque", "por", "pero", "con", "sin", "una", "uno", "unos",
    "unas", "del", "las", "los", "les", "sus", "sobre", "esto", "esta",
    "este", "eso", "esa", "ese", "aqui", "aquí", "ahi", "ahí", "hay",
    "ser", "estar", "tiene", "tengo", "puede", "puedo", "quiero", "hace",
    "haces", "hacer", "seria", "sería", "creo", "opinas", "piensas",
    "alguien", "ustedes", "todos", "todas", "hola", "jaja", "jeje",
})
_FOLLOW_UP_RE = re.compile(
    r"^(?:si|sí|no|claro|vale|ya|exacto|aja|ajá|y|pero|entonces|"
    r"por que|por qué|como asi|cómo así|y tu|y tú|que paso|qué pasó|"
    r"continua|continúa|sigue|despues|después|y luego)(?:\b|\s|[?!.,])",
    re.IGNORECASE,
)
_GENERIC_CONTEXT_RE = re.compile(
    r"\b(?:que opinas|qué opinas|que piensas|qué piensas|y tu|y tú|"
    r"que te parece|qué te parece|como lo ves|cómo lo ves|y despues|"
    r"y después|que paso|qué pasó)\b",
    re.IGNORECASE,
)
_INVITATION_RE = (
    # Explicit invitations addressed to the whole room.
    re.compile(
        r"\b(?:alguien|algun[oa]s?|ustedes|que opinan|que piensan|que creen)\b.*"
        r"\b(?:sabe|conoce|recomienda|opina|piensa|cree|ayuda|sugiere|consejo|idea|propuesta)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:alguna idea|alguna sugerencia|alguna recomendacion|que me recomiendan|"
        r"me ayudan|quien sabe|como lo ven|que harian|como resolverian|alguien podria)\b",
        re.IGNORECASE,
    ),
)

# Natural openings do not always contain an explicit "alguien" or "ustedes".
# These patterns cover collective opinions, choices and recommendations.
_OPEN_QUESTION_RE = (
    re.compile(
        r"\b(?:que|cual|como)\s+(?:opinan|piensan|les parece|prefieren|"
        r"elegirian|recomiendan|sugieren|harian|estan viendo|estan jugando|"
        r"estan escuchando|les gusta)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:que les parece|que tal les parece|con cual se quedan|"
        r"cual elegirian|como lo ven|que opcion prefieren)\b",
        re.IGNORECASE,
    ),
)

_SOFT_INVITATION_RE = (
    re.compile(
        r"\b(?:tengo|me surgio|me surge|traigo)\s+(?:una\s+)?"
        r"(?:duda|pregunta|idea)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:necesito|busco|acepto|se agradecen|me vendria bien)\s+"
        r"(?:(?:una|algunas|algunos|un par de)\s+)?"
        r"(?:ideas|opiniones|opinion|sugerencias|recomendaciones|consejos|ayuda)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:estoy entre|no se si elegir|no tengo claro si|"
        r"me cuesta decidir|no me decido entre)\b",
        re.IGNORECASE,
    ),
)

_ROLEPLAY_RE = re.compile(
    r"(?:\*[^*\n]{2,}\*|^\s*[—–-]\s*\S|"
    r"\b(?:roleplay|rolear|hagamos rol|modo rol|escena de rol|"
    r"mi personaje|tu personaje|personaje responde)\b)",
    re.IGNORECASE | re.MULTILINE,
)
_REPLY_KEYS = (
    "reply_to_message_id", "replyToMessageId", "replyMessageId",
    "reply_message_id", "referencedMessageId", "referenced_message_id",
    "quotedMessageId", "quoted_message_id",
)


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _tokens(value: str) -> set[str]:
    return {
        token for token in _TOKEN_RE.findall(_normalize(value))
        if token not in _STOPWORDS and len(token) >= 3
    }


def is_clear_invitation(content: str) -> bool:
    """Return True when a message naturally opens the conversation to the room."""
    text = " ".join(str(content or "").split())
    if len(text) < 12 or len(text) > 550 or text.startswith(("!", "-", "/")):
        return False
    normalized = _normalize(text)
    if any(pattern.search(normalized) for pattern in _INVITATION_RE):
        return True
    if any(pattern.search(normalized) for pattern in _SOFT_INVITATION_RE):
        return True
    return bool(
        ("?" in text or "¿" in text)
        and any(pattern.search(normalized) for pattern in _OPEN_QUESTION_RE)
    )



_LOW_SIGNAL_RE = re.compile(
    r"^(?:ok(?:ay)?|vale|dale|si|sí|no|claro|exacto|ya|jaja+|jeje+|"
    r"xd+|lmao|gracias|de nada|buenas|hola|buen dia|buenos dias)[.!?¡¿ ]*$"
    r"|^(?:hoy|ayer)\s+(?:estuve|he estado)\s+(?:jugando|trabajando|estudiando)"
    r"\s+(?:toda la tarde|todo el dia|todo el día|un rato)[.!? ]*$",
    re.IGNORECASE,
)
_SOCIAL_REACTION_RE = re.compile(
    r"\b(?:me encanta|me gusto|me gustó|me fascina|que bonito|qué bonito|"
    r"que genial|qué genial|que locura|qué locura|no me lo esperaba|"
    r"me dio risa|me hizo gracia|estoy feliz|que buena|qué buena)\b",
    re.IGNORECASE,
)


def is_socially_engageable(content: str) -> bool:
    """Identify substantive, ordinary chat messages worth an optional social read."""
    text = " ".join(str(content or "").split())
    if not text or len(text) > 700 or text.startswith(("!", "-", "/")):
        return False
    if _LOW_SIGNAL_RE.fullmatch(text):
        return False
    if _ROLEPLAY_RE.search(text):
        return False
    normalized = _normalize(text)
    if len(text) >= 12 and _SOCIAL_REACTION_RE.search(normalized):
        return True
    return len(_tokens(text)) >= 3


def _message_id(event: dict[str, Any]) -> str:
    return str(event.get("message_id") or event.get("messageId") or "").strip()


def _reply_to(event: dict[str, Any]) -> str:
    for key in _REPLY_KEYS:
        value = str(event.get(key) or "").strip()
        if value:
            return value
    return ""


def _timestamp(event: dict[str, Any]) -> int:
    try:
        return int(event.get("occurred_at_ms") or event.get("timestamp_ms") or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _is_follow_up(query: str) -> bool:
    normalized = " ".join(_normalize(query).split())
    # A fresh, specific question may contain only a few meaningful words once
    # Spanish stopwords are removed (for example: "¿Qué ventajas tiene X?").
    # Do not classify it as a follow-up solely by token count.
    is_short_fragment = len(normalized) <= 32 and len(_tokens(query)) <= 3
    is_explicit_follow_up = len(normalized) <= 72 and bool(
        _FOLLOW_UP_RE.search(normalized)
    )
    return is_short_fragment or is_explicit_follow_up


def _looks_roleplay(query: str) -> bool:
    return bool(_ROLEPLAY_RE.search(str(query or "").strip()))


def select_conversation_events(
    events: list[dict[str, Any]],
    *,
    query: str,
    actor_uid: str = "",
    reply_to_message_id: str = "",
    current_message_id: str = "",
    limit: int = 14,
    now_ms: int | None = None,
) -> list[dict[str, Any]]:
    """Select a reply chain or matching topic; input is newest-first, output chronological."""
    maximum = max(1, min(18, int(limit)))
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    current_id = str(current_message_id or "").strip()
    current_text = " ".join(str(query or "").split())
    current_uid = str(actor_uid or "")
    usable: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for item in events if isinstance(events, list) else []:
        if not isinstance(item, dict):
            continue
        event = dict(item)
        message_id = _message_id(event)
        content = " ".join(str(event.get("content") or "").split())
        if not content or (current_id and message_id == current_id):
            continue
        if message_id and message_id in seen_ids:
            continue
        if message_id:
            seen_ids.add(message_id)
        event["content"] = content
        stamp = _timestamp(event)
        if stamp and now - stamp > 24 * 60 * 60 * 1000:
            continue
        if current_text and str(event.get("actor_uid") or "") == current_uid:
            if content.casefold() == current_text.casefold():
                continue
        usable.append(event)

    chronological = sorted(
        usable, key=lambda item: (_timestamp(item), _message_id(item))
    )
    parent_id = str(reply_to_message_id or "").strip()
    if parent_id:
        by_id = {
            _message_id(item): item for item in chronological if _message_id(item)
        }
        chain_ids: set[str] = set()
        cursor = parent_id
        while cursor and cursor not in chain_ids:
            chain_ids.add(cursor)
            parent = by_id.get(cursor)
            cursor = _reply_to(parent) if parent else ""
        changed = True
        while changed:
            changed = False
            for item in chronological:
                message_id = _message_id(item)
                if (
                    message_id
                    and message_id not in chain_ids
                    and _reply_to(item) in chain_ids
                ):
                    chain_ids.add(message_id)
                    changed = True
        chain = [item for item in chronological if _message_id(item) in chain_ids]
        if chain:
            return chain[-maximum:]

    roleplay_indices = [
        index for index, item in enumerate(chronological)
        if _looks_roleplay(str(item.get("content") or ""))
    ]
    if _looks_roleplay(current_text) or (
        _is_follow_up(current_text) and bool(roleplay_indices)
    ):
        if roleplay_indices:
            last_scene_index = roleplay_indices[-1]
            scene_start = last_scene_index
            for index in range(last_scene_index - 1, -1, -1):
                newer_stamp = _timestamp(chronological[index + 1])
                older_stamp = _timestamp(chronological[index])
                if newer_stamp and older_stamp and newer_stamp - older_stamp > 90 * 60 * 1000:
                    break
                if _looks_roleplay(str(chronological[index].get("content") or "")):
                    scene_start = index
                elif index < last_scene_index - 8:
                    break
            scene = [
                item for item in chronological[scene_start:last_scene_index + 1]
                if _looks_roleplay(str(item.get("content") or ""))
            ]
            if scene:
                return scene[-maximum:]

    if _is_follow_up(current_text) or _GENERIC_CONTEXT_RE.search(_normalize(current_text)):
        recent = [
            item for item in chronological
            if not _timestamp(item) or now - _timestamp(item) <= 90 * 60 * 1000
        ]
        return recent[-min(maximum, 10):]

    query_tokens = _tokens(current_text)
    if not query_tokens:
        return []

    scored: list[tuple[float, int]] = []
    for index, item in enumerate(chronological):
        content_tokens = _tokens(str(item.get("content") or ""))
        shared = query_tokens.intersection(content_tokens)
        if not shared:
            continue
        rare_match = any(len(token) >= 7 for token in shared)
        score = len(shared) / max(
            1.0, (len(query_tokens) * len(content_tokens)) ** 0.5
        )
        if len(shared) >= 2 or rare_match or (len(query_tokens) <= 3 and shared):
            scored.append((score, index))

    if not scored:
        return []

    selected_indices = {
        index
        for _, index in sorted(
            scored, key=lambda pair: (pair[0], pair[1]), reverse=True
        )[:maximum]
    }
    return [
        chronological[index]
        for index in sorted(selected_indices)
    ][-maximum:]
