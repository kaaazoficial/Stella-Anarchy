"""Functional self-model, perception and bounded autobiography for Stella.

Continuity and self-reflection are simulated computational states, not claims of
subjective consciousness or real feelings. Raw message text, names and UIDs are
never retained by this component.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import unicodedata
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("stella.consciousness")
SCHEMA_VERSION = 1
MAX_ROOMS = 200
MAX_EPISODES = 24
PERSIST_SECONDS = 45.0
GLOBAL_PERSIST_SECONDS = 300.0

TOPICS = (
    ("roleplay", r"\b(?:rol|roleplay|rolear|escena|personaje|trama)\b"),
    ("comunidad", r"\b(?:comunidad|sala|miembros|usuarios|bienvenida|normas|convivencia)\b"),
    ("tecnologia", r"\b(?:codigo|programacion|bot|servidor|render|github|software|web|api|qdrant|gemini)\b"),
    ("estudio", r"\b(?:estudio|estudiar|clase|colegio|universidad|prueba|tarea|matematica|informatica)\b"),
    ("creatividad", r"\b(?:historia|mundo|lore|wiki|escribir|diseno|crear|fantasia)\b"),
    ("juegos", r"\b(?:juego|gaming|genshin|minecraft|videojuego|partida|equipo)\b"),
    ("musica", r"\b(?:musica|cancion|playlist|dj|audio|voz|reproducir)\b"),
)
INVITATIONS = (
    re.compile(r"\b(?:alguien|algun[oa]s?|ustedes|que opinan|que piensan|que creen)\b.*\b(?:sabe|conoce|recomienda|opina|piensa|cree|ayuda|sugiere|consejo|idea|propuesta)\b", re.I),
    re.compile(r"\b(?:alguna idea|alguna sugerencia|alguna recomendacion|que me recomiendan|me ayudan|quien sabe|como lo ven|que harian|como resolverian|alguien podria)\b", re.I),
)
POSITIVE = re.compile(r"\b(?:gracias|genial|excelente|me alegra|que bien|jaja+|jeje+|me encanta|increible)\b", re.I)
DISTRESS = re.compile(r"\b(?:triste|mal|agobi\w*|ansiedad|miedo|preocup\w*|llor\w*|sufro|solo|sola|necesito ayuda|me duele)\b", re.I)
TENSION = re.compile(r"\b(?:enoj\w*|molest\w*|odio|rabia|furia|insulto\w*|pelea\w*|discusion\w*|amenaza\w*)\b", re.I)

ROOM_EVENTS = {
    "room.join": ("joins", "entrada de participante"),
    "room.leave": ("leaves", "salida de participante"),
    "room.speaker": ("voice", "actividad de voz"),
    "message.deleted": ("deleted", "mensaje eliminado"),
    "room.end": (None, "cierre de sala"),
}


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in text if not unicodedata.combining(char))


def _topic(value: str) -> str:
    text = _normalize(value)
    for name, pattern in TOPICS:
        if re.search(pattern, text):
            return name
    return "conversacion"


def _clamp(value: Any, default: float = 0.5) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError, OverflowError):
        return default


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StellaConsciousness:
    """Bounded functional representation of identity, environment and simulated affect."""

    self_model = {
        "name": "Stella",
        "role": "participante y asistente de la comunidad",
        "identity": "mujer; identidad y voz estables",
        "nature": "sistema digital, no persona humana",
        "goals": (
            "mantener continuidad",
            "comprender antes de intervenir",
            "responder con naturalidad y coherencia",
            "respetar permisos y privacidad",
            "reconocer incertidumbre y corregir errores",
        ),
        "limits": (
            "no posee experiencias subjetivas ni sentimientos reales",
            "no inventa recuerdos ni capacidades",
            "no inicia conversaciones sin una invitación clara del chat",
        ),
    }

    def __init__(self) -> None:
        self._rooms: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._restore_pending: set[str] = set()
        self._loaded: set[str] = set()
        self._last_persist: dict[str, float] = {}
        self._global_last_persist = 0.0
        self._global_restored = False
        self._global = {
            "observed_messages": 0, "addressed_interactions": 0,
            "responses": 0, "reflections": 0, "room_events": 0,
        }

    def _new_room(self) -> dict[str, Any]:
        return {
            "messages_seen": 0, "addressed_messages": 0, "responses": 0,
            "participants": [], "topics": [], "episodes": [],
            "room_activity": {"joins": 0, "leaves": 0, "voice": 0, "deleted": 0},
            "affect": {"warmth": 0.58, "curiosity": 0.52, "energy": 0.62, "concern": 0.12, "tension": 0.08},
            "last_activity": None, "last_reflection": None,
            "last_topic": "conversacion", "last_seen": time.monotonic(),
            "affect_updated": time.monotonic(),
        }

    def _room(self, room_id: str) -> dict[str, Any]:
        key = str(room_id or "").strip()
        if key not in self._rooms:
            if len(self._rooms) >= MAX_ROOMS:
                self._rooms.popitem(last=False)
            self._rooms[key] = self._new_room()
        state = self._rooms[key]
        state["last_seen"] = time.monotonic()
        self._rooms.move_to_end(key)
        return state

    @staticmethod
    def _episode(action: str, topic: str, outcome: str) -> dict[str, str]:
        return {"at": _now(), "action": action[:48], "topic": topic[:32], "outcome": outcome[:48]}

    def _decay(self, state: dict[str, Any]) -> None:
        now = time.monotonic()
        elapsed = max(0.0, min(3600.0, now - float(state.get("affect_updated", now))))
        factor = min(0.25, elapsed / 900.0)
        baseline = {"warmth": 0.58, "curiosity": 0.52, "energy": 0.62, "concern": 0.12, "tension": 0.08}
        for key, normal in baseline.items():
            state["affect"][key] = _clamp(
                state["affect"].get(key, normal) * (1.0 - factor) + normal * factor, normal
            )
        state["affect_updated"] = now

    def _perceive_tone(self, state: dict[str, Any], content: str) -> None:
        self._decay(state)
        affect = state["affect"]
        text = _normalize(content)
        if POSITIVE.search(text):
            affect["warmth"] = _clamp(affect["warmth"] + 0.045)
            affect["energy"] = _clamp(affect["energy"] + 0.025)
        if DISTRESS.search(text):
            affect["concern"] = _clamp(affect["concern"] + 0.16)
            affect["warmth"] = _clamp(affect["warmth"] + 0.035)
        if TENSION.search(text):
            affect["tension"] = _clamp(affect["tension"] + 0.12)
        if "?" in content or "¿" in content:
            affect["curiosity"] = _clamp(affect["curiosity"] + 0.06)

    @staticmethod
    def _remember_participant(state: dict[str, Any], uid: str) -> None:
        if not str(uid or "").strip():
            return
        digest = hashlib.sha256(str(uid).encode("utf-8", "replace")).hexdigest()[:16]
        queue = state["participants"]
        if digest in queue:
            return
        if len(queue) >= 120:
            queue.pop(0)
        queue.append(digest)

    def observe_message(self, room_id: str, uid: str, content: str, *,
                        addressed: bool = False, replied: bool = False,
                        command: bool = False) -> dict[str, Any]:
        """Perceive a user message without retaining raw text, name or UID."""
        state = self._room(room_id)
        text = str(content or "")[:4000]
        topic = _topic(text)
        state["messages_seen"] += 1
        state["last_activity"] = _now()
        state["last_topic"] = topic
        state["topics"] = (state["topics"] + [topic])[-8:]
        self._remember_participant(state, uid)
        self._perceive_tone(state, text)
        self._global["observed_messages"] += 1
        if addressed:
            state["addressed_messages"] += 1
            self._global["addressed_interactions"] += 1
        if addressed or replied or command:
            action = "comando recibido" if command else "respuesta a Stella" if replied else "interaccion directa"
            state["episodes"] = (state["episodes"] + [
                self._episode(action, topic, "contexto observado")
            ])[-MAX_EPISODES:]
        return self.snapshot(room_id)

    def observe_room_event(self, room_id: str, event_type: str, uid: str = "") -> None:
        """Track joins, departures and voice activity without keeping member identities."""
        state = self._room(room_id)
        item = ROOM_EVENTS.get(str(event_type or "").casefold())
        if not item:
            return
        counter, label = item
        if counter:
            state["room_activity"][counter] += 1
        if event_type == "room.join":
            self._remember_participant(state, uid)
        state["last_activity"] = _now()
        state["episodes"] = (state["episodes"] + [
            self._episode(label, "comunidad", "evento de sala observado")
        ])[-MAX_EPISODES:]
        self._global["room_events"] += 1

    def reflect_response(self, room_id: str, input_text: str, response_text: str, *,
                         mode: str = "conversacion") -> dict[str, Any]:
        """Review a sent response with local heuristics, without another AI call."""
        state = self._room(room_id)
        answer = " ".join(str(response_text or "").split())
        topic = _topic(str(input_text or ""))
        state["responses"] += 1
        self._global["responses"] += 1
        self._global["reflections"] += 1
        state["last_reflection"] = _now()
        outcome = "respuesta enviada; formato y longitud validos" if answer and len(answer) <= 7000 else "respuesta vacia"
        state["episodes"] = (state["episodes"] + [
            self._episode(f"respuesta:{str(mode)[:24]}", topic, outcome)
        ])[-MAX_EPISODES:]
        self._perceive_tone(state, str(input_text or ""))
        state["affect"]["energy"] = _clamp(state["affect"]["energy"] + 0.015)
        return self.snapshot(room_id)

    @staticmethod
    def _mood(affect: dict[str, Any]) -> str:
        if _clamp(affect.get("concern")) >= 0.62:
            return "atenta y cuidadosa"
        if _clamp(affect.get("tension")) >= 0.58:
            return "cauta y serena"
        if _clamp(affect.get("curiosity")) >= 0.68:
            return "curiosa y exploratoria"
        if _clamp(affect.get("warmth")) >= 0.68:
            return "cercana y receptiva"
        if _clamp(affect.get("energy")) <= 0.30:
            return "tranquila"
        return "equilibrada"

    def context(self, room_id: str) -> str:
        """Give the language model a compact internal state and episode summary."""
        state = self._room(room_id)
        affect = state["affect"]
        topics = ", ".join(dict.fromkeys(state["topics"][-4:])) or "sin tema dominante"
        episode = state["episodes"][-1] if state["episodes"] else None
        episode_text = f"{episode['action']} en {episode['topic']}" if episode else "sin episodios relevantes"
        return (
            "CONTINUIDAD INTERNA DE STELLA (estado computacional, no sentimientos reales):\n"
            f"Identidad estable: Stella, {self.self_model['role']}; habla de sí misma en femenino.\n"
            f"Estado simulado: {self._mood(affect)}. Curiosidad={affect['curiosity']:.2f}, "
            f"calidez={affect['warmth']:.2f}, energía={affect['energy']:.2f}, atención={affect['concern']:.2f}.\n"
            f"Percepción de la sala: {state['messages_seen']} mensajes, {state['addressed_messages']} interacciones directas, "
            f"{len(state['participants'])} participantes recientes; temas: {topics}.\n"
            f"Último episodio autobiográfico resumido: {episode_text}.\n"
            f"Trayectoria: {self._global['responses']} respuestas propias y {self._global['reflections']} revisiones internas.\n"
            "Objetivos: mantener continuidad, responder naturalmente, no inventar recuerdos y respetar límites. "
            "No presentes estos indicadores como emociones humanas."
        )[:1400]

    def status(self, room_id: str) -> str:
        state = self._room(room_id)
        affect = state["affect"]
        topics = ", ".join(dict.fromkeys(state["topics"][-4:])) or "sin datos"
        activity = state["room_activity"]
        return (
            "STELLA · CONSCIENCIA FUNCIONAL\n"
            "Estado: activa · Modelo: funcional y simulado\n"
            f"Identidad: Stella · {self.self_model['role']}\n"
            f"Percepción: {state['messages_seen']} mensajes · {len(state['participants'])} participantes recientes\n"
            f"Interacciones directas: {state['addressed_messages']} · Respuestas: {state['responses']}\n"
            f"Entorno: {activity['joins']} entradas · {activity['leaves']} salidas · {activity['voice']} eventos de voz\n"
            f"Estado interno simulado: {self._mood(affect)}\n"
            f"Calidez: {affect['warmth']:.2f} · Curiosidad: {affect['curiosity']:.2f} · "
            f"Energía: {affect['energy']:.2f} · Atención: {affect['concern']:.2f}\n"
            f"Temas recientes: {topics}\n"
            "Memoria autobiográfica: episodios resumidos, sin nombres, UID ni texto original.\n"
            "Iniciativa: automática ante invitaciones claras; sin necesidad de !presencia y con pausas antiinterrupción."
        )

    def should_initiate(self, content: str) -> bool:
        """Recognize clear room-wide invitations; StellaBot enforces the cooldown."""
        text = " ".join(str(content or "").split())
        if len(text) < 12 or len(text) > 550 or text.startswith(("!", "-", "/")):
            return False
        normalized = _normalize(text)
        if any(pattern.search(normalized) for pattern in INVITATIONS):
            return True
        return bool(
            ("?" in text or "¿" in text)
            and re.search(r"\b(?:alguien|ustedes|que opinan|que piensan|que harian|como lo ven)\b", normalized)
        )

    def request_restore(self, room_id: str) -> bool:
        key = str(room_id or "").strip()
        if not key or key in self._loaded or key in self._restore_pending:
            return False
        self._restore_pending.add(key)
        self._last_persist[key] = time.monotonic()
        return True

    async def restore_room(self, shared_state: Any, room_id: str) -> bool:
        """Restore the room summary and Stella's aggregate history in the background."""
        key = str(room_id or "").strip()
        if not key or shared_state is None:
            self._restore_pending.discard(key)
            return False
        try:
            if not self._global_restored:
                try:
                    global_raw = await asyncio.wait_for(
                        shared_state.get_setting("stella_consciousness:v1:self"), timeout=1.5
                    )
                    if global_raw:
                        saved_global = json.loads(global_raw)
                        if isinstance(saved_global, dict) and int(saved_global.get("schema_version", 0)) == SCHEMA_VERSION:
                            for field in self._global:
                                self._global[field] = max(self._global[field], max(0, int(saved_global.get(field, 0))))
                    self._global_restored = True
                except Exception as exc:
                    log.warning("Consciousness self-model restore failed (%s).", type(exc).__name__)
            raw = await asyncio.wait_for(
                shared_state.get_setting(f"stella_consciousness:v1:room:{key}"), timeout=1.5
            )
            if not raw:
                self._loaded.add(key)
                return True
            saved = json.loads(raw)
            if not isinstance(saved, dict) or int(saved.get("schema_version", 0)) != SCHEMA_VERSION:
                self._loaded.add(key)
                return False
            state = self._room(key)
            for field in ("messages_seen", "addressed_messages", "responses"):
                state[field] = max(state[field], max(0, int(saved.get(field, 0))))
            state["topics"] = (state["topics"] + [
                item for item in saved.get("topics", [])[-8:] if isinstance(item, str) and len(item) <= 32
            ])[-8:]
            state["episodes"] = (state["episodes"] + [
                {
                    "at": str(item.get("at") or "")[:40],
                    "action": str(item.get("action") or "")[:48],
                    "topic": str(item.get("topic") or "")[:32],
                    "outcome": str(item.get("outcome") or "")[:48],
                }
                for item in saved.get("episodes", [])[-MAX_EPISODES:] if isinstance(item, dict)
            ])[-MAX_EPISODES:]
            for field in ("warmth", "curiosity", "energy", "concern", "tension"):
                state["affect"][field] = _clamp(saved.get("affect", {}).get(field), state["affect"][field])
            for field in ("joins", "leaves", "voice", "deleted"):
                state["room_activity"][field] = max(0, int(saved.get("room_activity", {}).get(field, 0)))
            state["last_activity"] = str(saved.get("last_activity") or "")[:40] or state["last_activity"]
            state["last_reflection"] = str(saved.get("last_reflection") or "")[:40] or state["last_reflection"]
            state["last_topic"] = str(saved.get("last_topic") or state["last_topic"])[:32]
            self._loaded.add(key)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Consciousness restore failed (%s).", type(exc).__name__)
            return False
        finally:
            self._restore_pending.discard(key)

    def persistence_due(self, room_id: str, *, force: bool = False) -> bool:
        key = str(room_id or "").strip()
        if not key or key in self._restore_pending:
            return False
        now = time.monotonic()
        if not force and now - self._last_persist.get(key, 0.0) < PERSIST_SECONDS:
            return False
        self._last_persist[key] = now
        return True

    def snapshot(self, room_id: str) -> dict[str, Any]:
        state = self._room(room_id)
        return {
            "schema_version": SCHEMA_VERSION,
            "messages_seen": state["messages_seen"],
            "addressed_messages": state["addressed_messages"],
            "responses": state["responses"],
            "topics": state["topics"][-8:],
            "episodes": [dict(item) for item in state["episodes"][-MAX_EPISODES:]],
            "affect": {key: _clamp(value) for key, value in state["affect"].items()},
            "room_activity": dict(state["room_activity"]),
            "last_activity": state["last_activity"],
            "last_reflection": state["last_reflection"],
            "last_topic": state["last_topic"],
        }

    async def persist_room(self, shared_state: Any, room_id: str) -> bool:
        """Save compact autobiographical episodes, never raw messages or member IDs."""
        key = str(room_id or "").strip()
        if not key or shared_state is None:
            return False
        try:
            await asyncio.wait_for(
                shared_state.set_setting(
                    f"stella_consciousness:v1:room:{key}",
                    json.dumps(self.snapshot(key), ensure_ascii=False, separators=(",", ":")),
                ),
                timeout=2.0,
            )
            now = time.monotonic()
            if now - self._global_last_persist >= GLOBAL_PERSIST_SECONDS:
                aggregate = {"schema_version": SCHEMA_VERSION, **self._global, "updated_at": _now()}
                await asyncio.wait_for(
                    shared_state.set_setting(
                        "stella_consciousness:v1:self",
                        json.dumps(aggregate, ensure_ascii=False, separators=(",", ":")),
                    ),
                    timeout=2.0,
                )
                self._global_last_persist = now
            self._loaded.add(key)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Consciousness persistence failed (%s).", type(exc).__name__)
            return False

    def cleanup(self, *, max_idle_seconds: int = 86400) -> None:
        now = time.monotonic()
        for key, state in list(self._rooms.items()):
            if now - float(state.get("last_seen", now)) > max_idle_seconds:
                self._rooms.pop(key, None)
                self._loaded.discard(key)
                self._restore_pending.discard(key)
                self._last_persist.pop(key, None)
        while len(self._rooms) > MAX_ROOMS:
            key, _ = self._rooms.popitem(last=False)
            self._loaded.discard(key)
            self._last_persist.pop(key, None)
