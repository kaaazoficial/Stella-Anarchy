"""Safe natural-language routing and short-lived community helpers for Stella."""
from __future__ import annotations

import re
import time
import unicodedata
from typing import Any


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip(" ¿?¡!.,;: ")


COMMAND_ALIASES: dict[str, tuple[str, list[str]]] = {}
NATURAL_COMMAND_NAMES = frozenset({
    "ayuda", "menu", "ping", "miuid", "perfil", "bio", "miestado",
    "puntos", "ranking", "diario", "daily", "trabajar", "pagar", "regalo", "regalarpuntos",
    "ruleta", "apostar", "aceptarapuesta", "rechazarapuesta", "misapuestas",
    "muro", "afinidad", "duelo", "azar", "frase", "chiste", "reto",
    "trivia", "responder", "adivina", "intento", "verdad", "8ball",
    "elige", "ship", "roast", "encuesta", "evento", "actividad",
    "advertir", "advertencias", "quitaradvertencia", "antispam",
    "respuesta", "respuestas", "eliminarmensajes", "centro", "admin", "nexus",
    "paneladmin", "estado", "sistema", "diagnostico", "fuentes", "saludfuentes",
    "metricas", "rendimiento", "autotest", "laboratorio", "perfilbot", "avatarbot",
    "silenciarchat", "hablarchat", "mencionar",
    "traducir", "ia", "asistente",
    "investigar", "analizar", "analizartexto", "calcular", "fecha",
    "zona", "join", "salirsala", "voz", "tts", "leer", "noleer",
    "dj", "reproducir", "silenciarchat", "hablarchat", "despertar",
})
NATURAL_EXTRA_COMMAND_NAMES = frozenset({
    "abrazo", "abrazar", "beso", "besar", "patada", "golpe", "cachetada",
    "acariciar", "mimar", "morder", "consolar", "felicitar", "saludar",
    "proteger", "bailar", "cosquillas", "chocar", "highfive", "ayudar",
    "regalar", "acompañar", "asustar", "curar", "cargar", "empujar",
    "regañar", "admirar", "apoyar", "dormir", "ignorar", "mirar", "invitar",
})


def parse_command_invocation(content: str, used_prefix: str) -> tuple[str, list[str]] | None:
    text = str(content or "").strip()
    prefix = str(used_prefix or "")
    if not text.startswith(prefix) or len(text) <= len(prefix):
        return None
    parts = text.split()
    command = parts[0][len(prefix):].casefold()
    args = parts[1:]
    alias = COMMAND_ALIASES.get(command)
    if alias:
        command, prepended = alias
        args = [*prepended, *args]
    return command, args


def interpret_direct_command(value: str) -> str | None:
    raw = re.sub(r"\s+", " ", str(value or "")).strip()
    if not raw:
        return None
    if raw.startswith(("!", "-")):
        parsed = parse_command_invocation(raw, raw[0])
        if not parsed:
            return None
        command, args = parsed
    else:
        first, _, rest = raw.partition(" ")
        command = _normalize(first)
        args = rest.split() if rest else []
    if command not in NATURAL_COMMAND_NAMES and command not in NATURAL_EXTRA_COMMAND_NAMES:
        return None
    return " ".join([command, *args]).strip()


def interpret_natural_request(value: str) -> str | None:
    raw = re.sub(r"\s+", " ", str(value or "")).strip()
    if not raw or len(raw) > 500 or raw.startswith(("!", "-")):
        return None
    match = re.match(r"^(?:oye[, ]+)?@?stella(?:[, :]+|$)\s*(.*)$", raw, flags=re.I)
    if not match:
        return None
    text = match.group(1).strip()
    normalized = _normalize(text)
    if not normalized:
        return None
    if normalized in {"ayuda", "help"}:
        return "asistente " + text[:300]
    patterns = (
        (r"^(?:que puedes hacer|que sabes hacer|comandos disponibles)$", "ayuda"),
        (r"^(?:muestra|abre|consulta) (?:mi )?perfil$", "perfil"),
        (r"^(?:muestra|consulta) (?:mis )?puntos$", "puntos"),
        (r"^(?:muestra|abre|consulta) (?:el )?ranking$", "ranking"),
        (r"^(?:dime|consulta) (?:la )?fecha$", "fecha"),
        (r"^(?:dime|cuentame) (?:un )?chiste$", "chiste"),
        (r"^(?:lanza|tira) (?:un )?dado$", "azar dado"),
        (r"^(?:lanza|tira) (?:una )?moneda$", "azar moneda"),
        (r"^(?:revisa|consulta|muestra) (?:el )?estado$", "estado"),
        (r"^(?:entra|unete) (?:a )?(?:la )?llamada$", "join"),
        (r"^(?:pausa|deten) (?:la )?musica$", "dj pausa"),
        (r"^(?:reanuda|continua) (?:la )?musica$", "dj reanudar"),
        (r"^(?:salta) (?:la )?(?:cancion|pista)$", "dj saltar"),
        (r"^(?:elimina|borra|limpia) (?:tus )?mensajes(?: propios)?$", "eliminarmensajes"),
    )
    for pattern, command in patterns:
        if re.fullmatch(pattern, normalized):
            return command
    if re.fullmatch(r"hora es en [a-z0-9_+/-]{2,64}", normalized):
        return "zona " + normalized.removeprefix("hora es en ")
    direct = interpret_direct_command(text)
    if direct:
        return direct
    return "asistente " + text[:300]


def should_execute_deterministic_fallback(request_text: str, fallback_command: str, provider_failed: bool = False) -> bool:
    command = str(fallback_command or "").strip()
    if not command or command.casefold().startswith("asistente "):
        return False
    if provider_failed:
        return True
    text = _normalize(request_text)
    return text.startswith((
        "ayuda", "comandos", "abre", "muestra", "consulta", "dime",
        "haz", "ejecuta", "pausa", "reanuda", "continua", "salta",
        "entra", "unete", "crea", "reproduce", "busca", "investiga",
        "analiza", "resume", "traduce", "lanza", "tira",
    ))


def extract_message_id(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in ("messageId", "message_id", "id"):
            value = payload.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value)
        for key in ("message", "data", "result", "item"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                found = extract_message_id(nested)
                if found:
                    return found
    return None


def cleanup_community_events(events: dict, now: float | None = None) -> int:
    stamp = time.time() if now is None else float(now)
    expired = [key for key, item in events.items() if not isinstance(item, dict) or item.get("closed") or float(item.get("expires_at", 0)) <= stamp]
    for key in expired:
        events.pop(key, None)
    return len(expired)


def toggle_community_participant(event: dict, user_id: str, nickname: str, join: bool, now: float | None = None) -> tuple[str, int]:
    stamp = time.time() if now is None else float(now)
    if not isinstance(event, dict) or event.get("closed"):
        return "closed", 0
    if float(event.get("expires_at", 0)) <= stamp:
        return "expired", 0
    participants = event.setdefault("participants", {})
    identity = str(user_id or "").strip()
    if not identity:
        return "invalid", len(participants)
    if join:
        if identity in participants:
            return "already_joined", len(participants)
        capacity = max(1, min(200, int(event.get("capacity", 40))))
        if len(participants) >= capacity:
            return "full", len(participants)
        participants[identity] = str(nickname or "Usuario")[:80]
        return "joined", len(participants)
    if identity not in participants:
        return "not_joined", len(participants)
    participants.pop(identity, None)
    return "left", len(participants)


def format_source_health(source_health: dict, limit: int = 12) -> str:
    if not isinstance(source_health, dict) or not source_health:
        return "FUENTES EXTERNAS\nTodavía no hay mediciones de fuentes en este proceso."
    lines = ["FUENTES EXTERNAS · SALUD DEL PROCESO"]
    ranked = sorted(source_health.items(), key=lambda item: (int((item[1] or {}).get("failures", 0)), int((item[1] or {}).get("requests", 0))), reverse=True)
    for host, raw in ranked[:max(1, min(20, int(limit)))]:
        item = raw if isinstance(raw, dict) else {}
        lines.append(f"• {str(host)[:80]} · HTTP {item.get('last_status') or '—'} · {float(item.get('last_latency_ms', 0) or 0):.0f} ms")
    return "\n".join(lines)
