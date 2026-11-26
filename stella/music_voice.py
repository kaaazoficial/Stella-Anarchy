from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import tempfile
import time
from collections import deque
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlparse

import httpx

log = logging.getLogger("stella.voice")

SAMPLE_RATE = 48000
CHANNELS = 1
MAX_TRACK_BYTES = 40 * 1024 * 1024
MAX_TRACK_SECONDS = 15 * 60
MAX_MUSIC_QUEUE = 10
VOICE_STATE_LIMIT = 100

TTS_VOICES = {
    "catalina": "es-CL-CatalinaNeural",
    "francisca": "es-CL-CatalinaNeural",
    "alvaro": "es-ES-AlvaroNeural",
    "elvira": "es-ES-ElviraNeural",
    "dalia": "es-MX-DaliaNeural",
    "jorge": "es-MX-JorgeNeural",
    "lucia": "es-AR-ElenaNeural",
    "carlos": "es-CO-GonzaloNeural",
}

# Licencias que permiten redistribución/uso musical bajo sus condiciones.
OPEN_LICENSE_MARKERS = (
    "creativecommons.org/licenses/by/",
    "creativecommons.org/licenses/by-sa/",
    "creativecommons.org/publicdomain/zero/",
    "creativecommons.org/publicdomain/mark/",
)
AUDIO_EXTENSIONS = {".mp3", ".ogg", ".oga", ".wav", ".flac", ".m4a", ".opus", ".aac"}
DJ_MUSIC_DIR = Path(os.getenv("DJ_MUSIC_DIR", "music/stella")).resolve()


def resolve_local_audio(filename: str) -> Path | None:
    """Resolve one local audio filename without permitting traversal or symlinks out."""
    safe = _safe_filename(filename)
    if not safe:
        return None
    root = DJ_MUSIC_DIR.resolve()
    path = (root / safe).resolve()
    try:
        if not path.is_relative_to(root) or not path.is_file():
            return None
        size = path.stat().st_size
        if size <= 0 or size > MAX_TRACK_BYTES:
            return None
    except OSError:
        return None
    return path


def list_local_audio() -> list[str]:
    try:
        DJ_MUSIC_DIR.mkdir(parents=True, exist_ok=True)
        return sorted(
            path.name for path in DJ_MUSIC_DIR.iterdir()
            if path.is_file()
            and path.suffix.casefold() in AUDIO_EXTENSIONS
            and resolve_local_audio(path.name) == path.resolve()
        )
    except OSError:
        return []


def _payload_data(result: dict[str, Any]) -> dict[str, Any]:
    data = result.get("data") or {}
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    return data if isinstance(data, dict) else {}


def _allowed_audio_url(url: str) -> bool:
    try:
        parsed = urlparse(str(url or ""))
        host = (parsed.hostname or "").casefold().rstrip(".")
        port = parsed.port
    except (TypeError, ValueError):
        return False
    allowed = (
        host == "archive.org"
        or host.endswith(".archive.org")
        or host == "jamendo.com"
        or host.endswith(".jamendo.com")
        or host == "upload.wikimedia.org"
        or host == "ccmixter.org"
        or host.endswith(".ccmixter.org")
        or host == "audius.co"
        or host.endswith(".audius.co")
    )
    return (
        parsed.scheme == "https"
        and port in (None, 443)
        and not parsed.username
        and not parsed.password
        and allowed
    )


def _license_label(value: str) -> str | None:
    license_url = str(value or "").strip().casefold().rstrip("/")
    if not license_url:
        return None
    if any(marker in license_url for marker in OPEN_LICENSE_MARKERS):
        if "/by-sa/" in license_url:
            return "Creative Commons BY-SA"
        if "/by/" in license_url:
            return "Creative Commons BY"
        if "/zero/" in license_url:
            return "CC0"
        if "/mark/" in license_url:
            return "Dominio público"
    return None


def _audius_license_label(value: str) -> tuple[str, str] | None:
    """Normalize only explicit permissive Audius license declarations."""
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().casefold()).strip("-")
    if not normalized:
        return None
    # NonCommercial and NoDerivatives declarations are intentionally excluded.
    if any(marker in normalized for marker in (
        "noncommercial", "non-commercial", "by-nc",
        "no-derivatives", "noderivatives", "by-nd",
    )):
        return None
    if "cc0" in normalized or normalized in {"public-domain", "publicdomain"}:
        return "CC0", "https://creativecommons.org/publicdomain/zero/1.0/"
    if (
        "by-sa" in normalized
        or "attribution-sharealike" in normalized
        or "attribution-share-alike" in normalized
        or normalized in {"share-alike", "sharealike"}
    ):
        return "Creative Commons BY-SA", "https://creativecommons.org/licenses/by-sa/4.0/"
    if "public-domain" in normalized:
        return "Dominio público", "https://creativecommons.org/publicdomain/mark/1.0/"
    if (
        normalized in {"by", "cc-by", "attribution"}
        or normalized.startswith("cc-by-")
        or normalized.startswith("attribution-")
        or "creative-commons-attribution" in normalized
    ):
        return "Creative Commons BY", "https://creativecommons.org/licenses/by/4.0/"
    return None


def _is_open_license(value: str) -> bool:
    """Validate either a source URL or a label already normalized by our catalog."""
    normalized = str(value or "").strip().casefold()
    return bool(_license_label(normalized)) or normalized in {
        "creative commons by", "creative commons by-sa", "cc0", "dominio público",
    }


def _safe_filename(value: str) -> str | None:
    name = str(value or "").strip()
    if not name or PurePosixPath(name).name != name or "\\" in name:
        return None
    if PurePosixPath(name).suffix.casefold() not in AUDIO_EXTENSIONS:
        return None
    return name


def _clean(value: Any, limit: int = 160) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))
    return " ".join(text.split())[:limit]


class MusicVoice:
    """Ephemeral LiveKit playback and TTS. No voice, queue or user data is persisted."""

    def __init__(self, platform: Any) -> None:
        self.platform = platform
        self.store = None
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(40.0, connect=8.0),
            follow_redirects=True,
            headers={"User-Agent": "StellaAnarchyCommunityBot/1.0"},
        )
        self.rooms: dict[str, dict[str, Any]] = {}
        self.search_results: dict[str, dict[str, Any]] = {}
        self.interaction_sessions: dict[tuple[str, str], dict[str, Any]] = {}
        self.connect_locks: dict[str, asyncio.Lock] = {}
        self.ready_events: dict[str, asyncio.Event] = {}
        self.ready_failures: set[str] = set()
        self._leave_api_requested: set[str] = set()
        self._rtc = None
        self._av = None
        self._np = None
        self._edge_tts = None
        self._load_errors: dict[str, str] = {}

    async def close(self) -> None:
        for room_id in list(self.rooms):
            await self.leave_room(room_id, request_leave=True)
        await self.http.aclose()

    def _bounded_setting(self, room_id: str, key: str, default: int, low: int, high: int) -> int:
        try:
            value = int(self.store.get_setting(room_id, key, str(default))) if self.store else default
        except (TypeError, ValueError):
            value = default
        return max(low, min(high, value))

    def _bool_setting(self, room_id: str, key: str, default: bool) -> bool:
        raw = self.store.get_setting(room_id, key, "1" if default else "0") if self.store else ("1" if default else "0")
        return str(raw).strip().casefold() in {"1", "true", "yes", "on", "si", "sí"}

    async def _send(self, room_id: str, text: str, reply_id: str | None = None) -> None:
        await self.platform.send_message(room_id, text[:1800], reply_id=reply_id)

    def _load_rtc(self) -> bool:
        if self._rtc is not None:
            return True
        try:
            from livekit import rtc
            self._rtc = rtc
            return True
        except Exception as exc:
            self._load_errors["livekit"] = type(exc).__name__
            return False

    def _load_audio(self, *, tts: bool = False) -> bool:
        if self._av is None or self._np is None:
            try:
                import av
                import numpy as np
                self._av = av
                self._np = np
            except Exception as exc:
                self._load_errors["audio"] = type(exc).__name__
                return False
        if tts and self._edge_tts is None:
            try:
                import edge_tts
                self._edge_tts = edge_tts
            except Exception as exc:
                self._load_errors["tts"] = type(exc).__name__
                return False
        return self._load_rtc()

    @staticmethod
    def _room_id(room: dict[str, Any]) -> str:
        return str(room.get("threadId") or room.get("thread_id") or "").strip()

    def notify_room_ready(self, room_id: str) -> None:
        """Release a pending publisher when Anarchy emits room.ready."""
        event = self.ready_events.get(str(room_id or "").strip())
        if event is not None:
            event.set()

    def notify_room_ended(self, room_id: str) -> None:
        """Fail a pending join immediately if the voice room closes."""
        thread_id = str(room_id or "").strip()
        event = self.ready_events.get(thread_id)
        if event is not None:
            self.ready_failures.add(thread_id)
            event.set()

    @staticmethod
    def _room_is_connected(room: Any, state: dict[str, Any] | None = None) -> bool:
        checker = getattr(room, "isconnected", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                pass
        return bool(state and state.get("connected"))

    async def _wait_livekit_connected(
        self, room_id: str, room: Any, state: dict[str, Any], *, timeout: float = 12.0
    ) -> None:
        ready = state.get("connection_ready")
        if ready is not None:
            await asyncio.wait_for(ready.wait(), timeout=timeout)
        if self.rooms.get(room_id) is not state or not self._room_is_connected(room, state):
            raise RuntimeError("LiveKit no está conectado para publicar o enviar audio.")

    @staticmethod
    def _is_transient_livekit_error(exc: Exception) -> bool:
        detail = f"{type(exc).__name__} {exc}".casefold()
        # Permission/authentication errors are deterministic, not reconnectable.
        if any(marker in detail for marker in (
            "http 401", "http 403", "status 401", "status 403",
            "permission denied", "not authorized", "unauthorized", "forbidden",
        )):
            return False
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return True
        return any(marker in detail for marker in (
            "publishtrackerror", "temporar", "not connected", "connection",
            "reconnect", "interrupted", "unavailable", "closed", "timeout",
            "deadline", "signal", "transport",
        ))

    async def _publish_track_with_retry(
        self, room_id: str, room: Any, state: dict[str, Any], track: Any,
        options: Any, *, stage: str,
    ) -> Any:
        for attempt in range(2):
            await self._wait_livekit_connected(room_id, room, state)
            try:
                return await room.local_participant.publish_track(track, options)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if attempt or not self._is_transient_livekit_error(exc):
                    raise
                log.warning(
                    "LiveKit publication had a transient error at %s for room %s; retrying once (%s).",
                    stage, room_id[:80], type(exc).__name__,
                )
                await asyncio.sleep(0.6)
        raise RuntimeError(f"LiveKit no pudo publicar el canal {stage}.")

    async def _close_audio_sources(self, state: dict[str, Any] | None) -> None:
        if not isinstance(state, dict) or state.get("_audio_resources_closed"):
            return
        state["_audio_resources_closed"] = True
        sources = []
        seen: set[int] = set()
        for key in ("audio_source", "music_source", "speech_source"):
            source = state.get(key)
            if source is None or id(source) in seen:
                continue
            seen.add(id(source))
            sources.append(source)
        for source in sources:
            closer = getattr(source, "aclose", None)
            if not callable(closer):
                closer = getattr(source, "close", None)
            if not callable(closer):
                continue
            try:
                result = closer()
                if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                    await result
            except Exception as exc:
                log.warning("LiveKit AudioSource cleanup failed (%s)", type(exc).__name__)

    async def ensure_room(self, room_id: str) -> tuple[bool, str]:
        thread_id = str(room_id or "").strip()
        if not thread_id:
            return False, "No pude identificar el chat que corresponde a la llamada."

        current = self.rooms.get(thread_id)
        if current:
            connection_state = str(current.get("connection_state") or "").casefold()
            if connection_state in {"connecting", "reconnecting"}:
                return False, "La conexión de voz todavía está iniciándose o recuperándose. Espera a que termine antes de volver a usar !join."
            if current.get("connected") and self._room_is_connected(current.get("room"), current):
                return True, "Stella ya está dentro de la llamada."
            await self.leave_room(thread_id, request_leave=True)

        lock = self.connect_locks.setdefault(thread_id, asyncio.Lock())
        try:
            async with lock:
                current = self.rooms.get(thread_id)
                if current:
                    connection_state = str(current.get("connection_state") or "").casefold()
                    if connection_state in {"connecting", "reconnecting"}:
                        return False, "La conexión de voz todavía está iniciándose o recuperándose."
                    if current.get("connected") and self._room_is_connected(current.get("room"), current):
                        return True, "Stella ya está dentro de la llamada."
                    await self.leave_room(thread_id, request_leave=True)

                rooms_result = await self.platform.request("GET", "/live-rooms")
                if not rooms_result.get("ok"):
                    return False, (
                        f"No pude consultar las llamadas disponibles (HTTP {rooms_result.get('status')}). "
                        "Comprueba que el token de Stella tenga acceso a la comunidad."
                    )
                rooms = _payload_data(rooms_result).get("roomList")
                if not isinstance(rooms, list):
                    return False, "Anarchy no devolvió una lista válida de llamadas."
                active = next(
                    (item for item in rooms if isinstance(item, dict) and self._room_id(item) == thread_id),
                    None,
                )
                if active is None:
                    return False, (
                        "No hay una llamada de voz activa para este chat. Abre primero una llamada "
                        "en Anarchy y luego vuelve a usar !join."
                    )
                if not self._load_rtc():
                    return False, (
                        "La dependencia LiveKit no está disponible. "
                        f"Detalle técnico: {self._load_errors.get('livekit', 'error de carga')}."
                    )

                ready_event = asyncio.Event()
                self.ready_events[thread_id] = ready_event
                self.ready_failures.discard(thread_id)
                joined_api = False
                stage = "create_room"
                room = None
                state: dict[str, Any] | None = None
                room_token = ""
                try:
                    result = await self.platform.request("POST", f"/live-rooms/{quote(thread_id, safe='')}/join")
                    if not result.get("ok"):
                        return False, f"Anarchy rechazó la entrada a la llamada (HTTP {result.get('status')})."
                    joined_api = True
                    self._leave_api_requested.discard(thread_id)
                    grant = _payload_data(result)
                    room_url = str(grant.get("url") or "")
                    room_token = str(grant.get("token") or "")
                    if not room_url or not room_token:
                        await self.platform.request("POST", f"/live-rooms/{quote(thread_id, safe='')}/leave")
                        return False, "Anarchy no entregó las credenciales temporales de LiveKit."

                    room = self._rtc.Room()
                    state = {
                        "room": room,
                        "audio_source": None,
                        "music_source": None,
                        "speech_source": None,
                        "audio_track": None,
                        "audio_publication_sid": None,
                        "capture_lock": asyncio.Lock(),
                        "speech_done": asyncio.Event(),
                        "connected": False,
                        "connection_state": "connecting",
                        "connection_ready": asyncio.Event(),
                        "connection_error": None,
                        "music_queue": deque(),
                        "music_task": None,
                        "music_current": None,
                        "music_paused": asyncio.Event(),
                        "music_skip": asyncio.Event(),
                        "music_volume": 0.75,
                        "repeat_mode": "off",
                        "transition_ms": self._bounded_setting(thread_id, "dj_transition_ms", 500, 0, 2000),
                        "duck_enabled": self._bool_setting(thread_id, "dj_duck_enabled", True),
                        "music_duck_gain": 0.28,
                        "tts_active": asyncio.Event(),
                        "speech_queue": asyncio.Queue(maxsize=15),
                        "speech_task": None,
                        "tts_enabled": False,
                        "tts_voice": os.getenv("TTS_VOICE", "es-CL-CatalinaNeural"),
                        "tts_rate": "+0%",
                        "tts_volume": 0.75,
                        "last_tts_volume": 0.75,
                        "tts_read_links": bool(self.store and self.store.get_setting(thread_id, "tts_read_links", "0") == "1"),
                        "tts_read_mentions": bool(self.store and self.store.get_setting(thread_id, "tts_read_mentions", "0") == "1"),
                        "created_at": time.monotonic(),
                    }
                    state["music_paused"].set()
                    state["speech_done"].set()
                    self.rooms[thread_id] = state

                    def on_disconnected(*args):
                        existing = self.rooms.get(thread_id)
                        if existing is not state:
                            return
                        reason = args[0] if args else None
                        state["connected"] = False
                        state["connection_state"] = "disconnected"
                        state["connection_ready"].clear()
                        state["music_paused"].clear()
                        log.warning(
                            "LiveKit disconnected for room %s (reason=%s).",
                            thread_id[:80],
                            str(reason)[:120] if reason is not None else "unspecified",
                        )
                        cleanup = state.get("cleanup_task")
                        if cleanup is None or cleanup.done():
                            state["cleanup_task"] = asyncio.create_task(
                                self.leave_room(thread_id, request_leave=True)
                            )

                    def on_reconnecting(*_args):
                        if self.rooms.get(thread_id) is state:
                            state["connection_state"] = "reconnecting"
                            state["connection_ready"].clear()
                            state["music_paused"].clear()
                            log.warning("LiveKit is reconnecting for room %s.", thread_id[:80])

                    def on_reconnected(*_args):
                        if self.rooms.get(thread_id) is state:
                            state["connection_state"] = "connected"
                            state["connected"] = True
                            state["connection_ready"].set()
                            state["music_paused"].set()
                            log.info("LiveKit reconnected for room %s.", thread_id[:80])

                    def on_connection_state_changed(connection_state, *_args):
                        if self.rooms.get(thread_id) is not state:
                            return
                        raw_state = getattr(connection_state, "name", connection_state)
                        normalized = str(raw_state).casefold()
                        state["connection_state"] = normalized
                        if "reconnect" in normalized or "connecting" in normalized:
                            state["connection_ready"].clear()
                            state["music_paused"].clear()
                        elif "connected" in normalized and "disconnected" not in normalized:
                            state["connected"] = True
                            state["connection_ready"].set()
                            state["music_paused"].set()

                    room.on("disconnected", on_disconnected)
                    room.on("reconnecting", on_reconnecting)
                    room.on("reconnected", on_reconnected)
                    room.on("connection_state_changed", on_connection_state_changed)

                    stage = "connect"
                    room_options_factory = getattr(self._rtc, "RoomOptions", None)
                    if callable(room_options_factory):
                        room_options = room_options_factory(
                            auto_subscribe=False,
                            connect_timeout=20.0,
                        )
                        await room.connect(room_url, room_token, options=room_options)
                    else:
                        await room.connect(room_url, room_token)
                    if self.rooms.get(thread_id) is not state:
                        raise RuntimeError("LiveKit se desconectó durante la conexión.")

                    # Anarchy emits room.ready only after the server-side voice room is prepared.
                    # Publishing immediately after room.connect() can time out with PublishTrackError.
                    stage = "wait_room_ready"
                    await asyncio.wait_for(ready_event.wait(), timeout=20.0)
                    if thread_id in self.ready_failures:
                        raise RuntimeError("La llamada terminó antes de quedar lista para publicar audio.")
                    if self.rooms.get(thread_id) is not state or not self._room_is_connected(room, state):
                        raise RuntimeError("LiveKit se desconectó antes de que Anarchy marcara la sala como lista.")

                    state["connection_state"] = "connected"
                    state["connected"] = True
                    state["connection_ready"].set()
                    stage = "create_audio_sources"
                    music_source = self._rtc.AudioSource(
                        SAMPLE_RATE, CHANNELS, queue_size_ms=1000
                    )
                    speech_source = self._rtc.AudioSource(
                        SAMPLE_RATE, CHANNELS, queue_size_ms=100
                    )
                    state["audio_source"] = music_source
                    state["music_source"] = music_source
                    state["speech_source"] = speech_source
                    music_track = self._rtc.LocalAudioTrack.create_audio_track(
                        "stella-music", music_source
                    )
                    speech_track = self._rtc.LocalAudioTrack.create_audio_track(
                        "stella-tts", speech_source
                    )
                    state["audio_track"] = music_track
                    state["speech_audio_track"] = speech_track

                    stage = "publish_music"
                    music_publication = await self._publish_track_with_retry(
                        thread_id, room, state, music_track,
                        self._rtc.TrackPublishOptions(
                            source=self._rtc.TrackSource.SOURCE_MICROPHONE
                        ),
                        stage="música",
                    )
                    state["audio_publication_sid"] = getattr(music_publication, "sid", None)

                    stage = "publish_speech"
                    speech_publication = await self._publish_track_with_retry(
                        thread_id, room, state, speech_track,
                        self._rtc.TrackPublishOptions(
                            source=self._rtc.TrackSource.SOURCE_MICROPHONE
                        ),
                        stage="lectura",
                    )
                    state["speech_publication_sid"] = getattr(speech_publication, "sid", None)
                    if self.rooms.get(thread_id) is not state or not self._room_is_connected(room, state):
                        raise RuntimeError("La conexión LiveKit se perdió después de publicar los canales de audio.")

                    log.info(
                        "LiveKit connected for room %s; music and TTS tracks published (music_sid=%s, speech_sid=%s).",
                        thread_id[:80],
                        str(state.get("audio_publication_sid") or "unavailable")[:80],
                        str(state.get("speech_publication_sid") or "unavailable")[:80],
                    )
                    return True, "Stella se unió a la llamada y publicó sus canales independientes de música y lectura."
                except Exception as exc:
                    detail = str(exc or "").replace(room_token, "[redacted]")[:320]
                    log.warning(
                        "LiveKit join failed at stage=%s (%s): %s",
                        stage,
                        type(exc).__name__,
                        detail or "sin detalle del SDK",
                    )
                    if state is not None and self.rooms.get(thread_id) is state:
                        await self.leave_room(thread_id, request_leave=joined_api)
                    else:
                        if room is not None:
                            try:
                                await room.disconnect()
                            except Exception:
                                pass
                        await self._close_audio_sources(state)
                        if joined_api and not (state and state.get("_leave_requested")):
                            await self.platform.request(
                                "POST", f"/live-rooms/{quote(thread_id, safe='')}/leave"
                            )
                    return False, f"No pude conectarme a LiveKit durante {stage} ({type(exc).__name__})."
        finally:
            self.ready_events.pop(thread_id, None)
            self.ready_failures.discard(thread_id)
            if self.rooms.get(thread_id) is None and not lock.locked():
                self.connect_locks.pop(thread_id, None)

    async def leave_room(self, room_id: str, *, request_leave: bool) -> None:
        thread_id = str(room_id or "").strip()
        if not thread_id:
            return
        should_request_leave = request_leave and thread_id not in self._leave_api_requested
        if should_request_leave:
            # Mark before any await so concurrent disconnect callbacks cannot duplicate it.
            self._leave_api_requested.add(thread_id)
        state = self.rooms.pop(thread_id, None)
        if state:
            state["connected"] = False
            state["connection_state"] = "disconnecting"
            current = asyncio.current_task()
            for key in ("music_task", "speech_task"):
                task = state.get(key)
                if task and task is not current and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        log.exception("Could not stop Stella voice worker")
            room = state.get("room")
            if room is not None:
                try:
                    await room.disconnect()
                except Exception:
                    log.exception("Could not disconnect Stella from LiveKit")
            await self._close_audio_sources(state)

        # Mark before awaiting the API call so concurrent disconnect callbacks
        # cannot issue duplicate leave requests for the same room.
        if should_request_leave:
            try:
                result = await self.platform.request(
                    "POST", f"/live-rooms/{quote(thread_id, safe='')}/leave"
                )
                if not result.get("ok"):
                    self._leave_api_requested.discard(thread_id)
                    log.warning(
                        "Anarchy voice leave returned HTTP %s for room %s.",
                        result.get("status"),
                        thread_id[:80],
                    )
            except Exception as exc:
                self._leave_api_requested.discard(thread_id)
                log.warning("Could not leave Anarchy voice room (%s).", type(exc).__name__)

        lock = self.connect_locks.get(thread_id)
        if lock is not None and not lock.locked():
            self.connect_locks.pop(thread_id, None)
        if len(self._leave_api_requested) > 500:
            self._leave_api_requested.clear()

    @staticmethod
    def _frame_samples(frame: Any, volume: float, np: Any):
        samples = frame.to_ndarray().reshape(-1).astype(np.float32, copy=False)
        if volume != 1.0:
            samples = samples * max(0.0, min(1.0, float(volume)))
        return np.clip(samples, -32768, 32767).astype(np.int16, copy=False)

    async def _capture_pcm(
        self, source: Any, frame: Any, samples: Any, capture_lock: asyncio.Lock | None = None
    ) -> None:
        flat = self._np.asarray(samples, dtype=self._np.int16).reshape(-1)
        # LiveKit's realtime audio example uses 10 ms PCM frames at 48 kHz.
        for offset in range(0, int(flat.size), 480):
            chunk = flat[offset:offset + 480]
            if chunk.size == 0:
                continue
            rtc_frame = self._rtc.AudioFrame.create(
                SAMPLE_RATE, CHANNELS, int(chunk.size)
            )
            destination = self._np.frombuffer(rtc_frame.data, dtype=self._np.int16)
            destination[: chunk.size] = chunk
            if capture_lock is None:
                await source.capture_frame(rtc_frame)
            else:
                async with capture_lock:
                    await source.capture_frame(rtc_frame)

    async def _play_audio_bytes(
        self, source: Any, data: bytes, volume: float, *, room_id: str,
        skip_event=None, pause_event=None, fade_ms: int = 0,
        duck_state: dict[str, Any] | None = None,
        capture_lock: asyncio.Lock | None = None,
    ) -> None:
        if not self._load_audio():
            raise RuntimeError("No se pudieron cargar PyAV/NumPy para reproducir audio.")
        if len(data) > MAX_TRACK_BYTES:
            raise RuntimeError("El archivo supera el límite de 40 MB.")
        container = self._av.open(io.BytesIO(data))
        try:
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is None:
                raise RuntimeError("El archivo no contiene una pista de audio.")
            resampler = self._av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
            try:
                duration = float(stream.duration * stream.time_base) if stream.duration and stream.time_base else 0.0
            except (TypeError, ValueError, OverflowError):
                duration = 0.0
            total_samples = int(duration * SAMPLE_RATE) if duration > 0 else None
            fade_samples = int(SAMPLE_RATE * max(0, min(2000, int(fade_ms))) / 1000)
            position = 0
            started = time.monotonic()

            async def emit(frame) -> None:
                nonlocal position
                if skip_event is not None and skip_event.is_set():
                    return
                if pause_event is not None:
                    if callable(pause_event):
                        await pause_event()
                    else:
                        await pause_event.wait()
                duck_active = bool(
                    duck_state
                    and duck_state.get("duck_enabled", True)
                    and duck_state.get("tts_active")
                    and duck_state["tts_active"].is_set()
                )
                duck_gain = float(duck_state.get("music_duck_gain", 0.28)) if duck_active else 1.0
                samples = self._frame_samples(frame, volume * duck_gain, self._np)
                if samples.size and fade_samples:
                    fade_in = min(1.0, max(0.0, (position + samples.size) / fade_samples))
                    fade_out = 1.0
                    if total_samples is not None:
                        remaining = max(0, total_samples - position)
                        if remaining < fade_samples:
                            fade_out = min(1.0, remaining / fade_samples)
                    gain = min(fade_in, fade_out)
                    if gain < 0.999:
                        samples = self._np.clip(
                            samples.astype(self._np.float32) * gain,
                            -32768, 32767,
                        ).astype(self._np.int16)
                if samples.size:
                    await self._capture_pcm(source, frame, samples, capture_lock=capture_lock)
                    position += int(samples.size)

            for decoded in container.decode(stream):
                if time.monotonic() - started > MAX_TRACK_SECONDS:
                    raise RuntimeError("La reproducción alcanzó el límite de 15 minutos.")
                for frame in resampler.resample(decoded):
                    if skip_event is not None and skip_event.is_set():
                        return
                    await emit(frame)
            for frame in resampler.resample(None):
                if skip_event is not None and skip_event.is_set():
                    return
                await emit(frame)
        finally:
            container.close()

    async def _download_audio(self, url: str) -> bytes:
        if not _allowed_audio_url(url):
            raise RuntimeError("El catálogo proporcionó un dominio de audio no autorizado.")
        total = 0
        chunks: list[bytes] = []
        async with self.http.stream("GET", url) as response:
            if not response.is_success:
                raise RuntimeError(f"La fuente musical respondió HTTP {response.status_code}.")
            if not _allowed_audio_url(str(response.url)):
                raise RuntimeError("La descarga fue redirigida a un dominio no autorizado.")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].casefold()
            if content_type and not (content_type.startswith("audio/") or content_type in {"application/octet-stream", "application/ogg"}):
                raise RuntimeError("La fuente no devolvió un archivo de audio.")
            length = response.headers.get("content-length")
            if length and length.isdigit() and int(length) > MAX_TRACK_BYTES:
                raise RuntimeError("La pista supera el límite de 40 MB.")
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_TRACK_BYTES:
                    raise RuntimeError("La pista supera el límite de 40 MB.")
                chunks.append(chunk)
        data = b"".join(chunks)
        if not data:
            raise RuntimeError("La fuente devolvió un archivo vacío.")
        return data

    async def search_archive(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search Archive with token fallback; return only verified open-license audio."""
        terms = re.findall(r"[\wáéíóúñü-]+", str(query or ""), flags=re.I)[:8]
        if not terms:
            return []

        groups: list[list[str]] = []
        for group in (terms[:3], terms[3:6], terms[:1]):
            if group and group not in groups:
                groups.append(group)

        results: list[dict[str, Any]] = []
        seen_identifiers: set[str] = set()
        semaphore = asyncio.Semaphore(3)

        async def inspect_item(identifier: str) -> dict[str, Any] | None:
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", identifier):
                return None
            async with semaphore:
                try:
                    metadata_response = await self.http.get(
                        f"https://archive.org/metadata/{quote(identifier, safe='')}"
                    )
                    if not metadata_response.is_success:
                        return None
                    payload = metadata_response.json()
                    if not isinstance(payload, dict):
                        return None
                    metadata = payload.get("metadata") or {}
                    if not isinstance(metadata, dict):
                        return None
                    license_url = str(metadata.get("licenseurl") or metadata.get("license_url") or "")
                    license_label = _license_label(license_url)
                    if not license_label:
                        return None

                    candidates: list[tuple[int, str]] = []
                    for item in payload.get("files") or []:
                        if not isinstance(item, dict) or item.get("private") is True:
                            continue
                        filename = _safe_filename(str(item.get("name") or ""))
                        if not filename:
                            continue
                        try:
                            size = int(item.get("size") or 0)
                        except (TypeError, ValueError, OverflowError):
                            size = 0
                        if size > MAX_TRACK_BYTES:
                            continue
                        candidates.append((size or MAX_TRACK_BYTES, filename))
                    if not candidates:
                        return None
                    candidates.sort(key=lambda item: (item[0], item[1]))
                    filename = candidates[0][1]

                    server = str(payload.get("d1") or "").strip()
                    directory = str(payload.get("dir") or "")
                    host = (urlparse("https://" + server).hostname or "").casefold()
                    safe_parts = [
                        part for part in directory.strip("/").split("/")
                        if part and part not in {".", ".."}
                    ]
                    if server and host.endswith(".archive.org") and safe_parts and directory.startswith("/"):
                        base = "https://" + host + "/" + "/".join(quote(part, safe="") for part in safe_parts)
                        audio_url = base + "/" + quote(filename, safe="")
                    else:
                        audio_url = (
                            f"https://archive.org/download/{quote(identifier, safe='')}/"
                            f"{quote(filename, safe='')}"
                        )
                    if not _allowed_audio_url(audio_url):
                        return None
                    return {
                        "identifier": identifier,
                        "title": _clean(metadata.get("title") or identifier),
                        "creator": _clean(metadata.get("creator") or metadata.get("artist") or "Autor no indicado"),
                        "license": license_label,
                        "license_url": license_url,
                        "item_url": f"https://archive.org/details/{quote(identifier, safe='')}",
                        "url": audio_url,
                        "source": "Internet Archive",
                    }
                except (httpx.HTTPError, ValueError, TypeError, KeyError, OverflowError):
                    log.debug("Archive item validation failed for %s.", identifier, exc_info=True)
                    return None

        for group in groups:
            clauses = [
                f"{field}:{term}"
                for term in group
                for field in ("title", "subject", "description")
            ]
            lucene = "mediatype:audio AND (" + " OR ".join(clauses) + ")"
            response = await self.http.get(
                "https://archive.org/advancedsearch.php",
                params={
                    "q": lucene,
                    "fl[]": ["identifier"],
                    "rows": max(15, min(30, limit * 6)),
                    "page": 1,
                    "output": "json",
                    "sort[]": "downloads desc",
                },
            )
            response.raise_for_status()
            payload = response.json()
            docs = (payload.get("response") or {}).get("docs") or []
            identifiers: list[str] = []
            for doc in docs:
                if not isinstance(doc, dict):
                    continue
                identifier = str(doc.get("identifier") or "")
                if identifier and identifier not in seen_identifiers:
                    seen_identifiers.add(identifier)
                    identifiers.append(identifier)
            if not identifiers:
                continue

            checked = await asyncio.gather(
                *(inspect_item(identifier) for identifier in identifiers[:max(12, limit * 4)]),
                return_exceptions=True,
            )
            for track in checked:
                if isinstance(track, dict):
                    results.append(track)
                    if len(results) >= limit:
                        return results[:limit]
        return results[:limit]

    async def search_wikimedia(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search Wikimedia Commons and accept only audio with verified CC/PD metadata."""
        terms = re.findall(r"[\wáéíóúñü-]+", str(query or ""), flags=re.I)[:5]
        if not terms:
            return []
        groups = [terms[:3], terms[:1]]
        results: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        for group in groups:
            if not group:
                continue
            response = await self.http.get(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action": "query",
                    "format": "json",
                    "generator": "search",
                    "gsrsearch": " ".join(group) + " filetype:audio",
                    "gsrnamespace": 6,
                    "gsrlimit": max(10, min(30, limit * 4)),
                    "prop": "imageinfo",
                    "iiprop": "url|size|mime|mediatype|extmetadata",
                    "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|Credit|ObjectName",
                },
                headers={
                    "Accept": "application/json",
                    "User-Agent": (
                        "StellaAnarchy/1.0 "
                        "(https://github.com/kaaazoficial/Stella-Anarchy"
                        + (
                            "; " + os.getenv("STELLA_CONTACT_EMAIL", "").strip()
                            if os.getenv("STELLA_CONTACT_EMAIL", "").strip()
                            else ""
                        )
                        + ")"
                    ),
                },
            )
            response.raise_for_status()
            pages = (response.json().get("query") or {}).get("pages") or {}
            if not isinstance(pages, dict):
                continue
            for page in pages.values():
                if not isinstance(page, dict):
                    continue
                title = str(page.get("title") or "")
                if Path(title).suffix.casefold() not in AUDIO_EXTENSIONS:
                    continue
                info_list = page.get("imageinfo") or []
                if not info_list or not isinstance(info_list[0], dict):
                    continue
                info = info_list[0]
                mime = str(info.get("mime") or "").casefold()
                media_type = str(info.get("mediatype") or "").upper()
                if media_type not in {"", "AUDIO"} or (mime and not mime.startswith("audio/")):
                    continue
                try:
                    size = int(info.get("size") or 0)
                except (TypeError, ValueError, OverflowError):
                    size = 0
                if size > MAX_TRACK_BYTES:
                    continue
                metadata = info.get("extmetadata") or {}
                if not isinstance(metadata, dict):
                    continue

                def meta_value(key: str) -> str:
                    item = metadata.get(key) or {}
                    return str(item.get("value") or "") if isinstance(item, dict) else str(item or "")

                license_url = meta_value("LicenseUrl").strip()
                license_label = _license_label(license_url)
                short_license = meta_value("LicenseShortName").casefold()
                if not license_label and ("public domain" in short_license or short_license == "pdm"):
                    license_label = "Dominio público"
                if not license_label and "cc0" in short_license:
                    license_label = "CC0"
                audio_url = str(info.get("url") or "").strip()
                if not license_label or not _allowed_audio_url(audio_url) or audio_url in seen_urls:
                    continue
                item_url = str(info.get("descriptionurl") or "").strip()
                if not item_url.startswith("https://commons.wikimedia.org/"):
                    item_url = "https://commons.wikimedia.org/wiki/" + quote(title.replace(" ", "_"), safe=":/")
                results.append({
                    "identifier": str(page.get("pageid") or title),
                    "title": _clean(meta_value("ObjectName") or title.removeprefix("File:")),
                    "creator": _clean(meta_value("Artist") or meta_value("Credit") or "Autor no indicado"),
                    "license": license_label,
                    "license_url": license_url,
                    "item_url": item_url,
                    "url": audio_url,
                    "source": "Wikimedia Commons",
                })
                seen_urls.add(audio_url)
                if len(results) >= limit:
                    return results[:limit]
            if results:
                break
        return results[:limit]

    async def search_openverse(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search Openverse's music catalog for CC0/PDM/CC-BY/CC-BY-SA audio."""
        term = " ".join(re.findall(r"[\wáéíóúñü-]+", str(query or ""), flags=re.I)[:5])
        if not term:
            return []
        response = await self.http.get(
            "https://api.openverse.org/v1/audio/",
            params={
                "q": term,
                "category": "music",
                "license_type": "commercial",
                "license": "cc0,pdm,by,by-sa",
                "extension": "mp3,ogg,oga,flac,wav,m4a,aac,opus",
                "length": "medium,long",
                "page": 1,
                "page_size": max(10, min(20, limit * 4)),
                "filter_dead": "true",
                "mature": "false",
            },
            headers={"Accept": "application/json", "User-Agent": "Stella-Anarchy/1.0 (licensed audio catalog)"},
        )
        response.raise_for_status()
        payload = response.json()
        records = payload.get("results") or []
        labels = {
            "cc0": "CC0 · Dominio público",
            "pdm": "Public Domain Mark",
            "by": "CC BY · Requiere atribución",
            "by-sa": "CC BY-SA · Requiere atribución y compartir igual",
        }
        results: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for item in records:
            if not isinstance(item, dict):
                continue
            license_code = str(item.get("license") or "").casefold()
            license_label = labels.get(license_code)
            license_url = str(item.get("license_url") or "").strip()
            if not license_label or not license_url.startswith("https://creativecommons.org/"):
                continue
            if _license_label(license_url) is None and license_code not in {"pdm"}:
                continue
            audio_url = str(item.get("url") or "").strip()
            if not _allowed_audio_url(audio_url) or audio_url in seen_urls:
                continue
            try:
                extension = Path(urlparse(audio_url).path).suffix.casefold()
                duration_ms = int(item.get("duration") or 0)
                size = int(item.get("filesize") or 0)
            except (TypeError, ValueError, OverflowError):
                continue
            if extension not in AUDIO_EXTENSIONS or duration_ms < 30000 or size > MAX_TRACK_BYTES:
                continue
            try:
                landing = urlparse(str(item.get("foreign_landing_url") or "").strip())
                landing_port = landing.port
            except (TypeError, ValueError):
                continue
            if (
                landing.scheme != "https"
                or not landing.hostname
                or landing.username
                or landing.password
                or landing_port not in (None, 443)
            ):
                continue
            title = _clean(item.get("title") or "Pista de Openverse")
            creator = _clean(item.get("creator") or "Autor no indicado")
            credit = _clean(item.get("attribution") or "")
            if not credit:
                credit = f"{title} — {creator} · {labels[license_code]} · {landing.geturl()}"
            results.append({
                "identifier": str(item.get("id") or audio_url),
                "title": title,
                "creator": creator,
                "license": labels[license_code],
                "license_url": license_url,
                "item_url": landing.geturl(),
                "url": audio_url,
                "credit_line": credit,
                "source": "Openverse",
            })
            seen_urls.add(audio_url)
            if len(results) >= limit:
                break
        return results[:limit]

    @staticmethod
    def _ccmixter_rows(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            for key in ("results", "data", "items", "response"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [row for row in value if isinstance(row, dict)]
        return []

    @staticmethod
    def _ccmixter_license(info: dict[str, Any]) -> tuple[str, str] | None:
        license_url = str(info.get("license_url") or info.get("licenseurl") or "").strip()
        label = _license_label(license_url)
        if label:
            return label, license_url
        name = re.sub(r"[^a-z0-9]+", "-", str(info.get("license_name") or "").casefold()).strip("-")
        if name in {"public-domain", "public-domain-dedication", "pd"}:
            return "Dominio público", "https://creativecommons.org/publicdomain/mark/1.0/"
        if name in {"attribution-sharealike", "attribution-share-alike", "cc-by-sa", "by-sa"}:
            return "Creative Commons BY-SA", "https://creativecommons.org/licenses/by-sa/4.0/"
        if name in {"attribution", "cc-by", "by"}:
            return "Creative Commons BY", "https://creativecommons.org/licenses/by/4.0/"
        return None

    async def search_ccmixter(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search ccMixter's official API and verify each upload's license and audio file."""
        terms = re.findall(r"[\wáéíóúñü-]+", str(query or ""), flags=re.I)[:8]
        if not terms:
            return []
        search_text = " ".join(terms)
        base = "https://ccmixter.org/api/query"

        async def search_license(license_code: str) -> list[dict[str, Any]]:
            response = await self.http.get(
                base,
                params={
                    "f": "json",
                    "search": search_text,
                    "search_type": "any",
                    "lic": license_code,
                    "limit": max(6, min(12, limit * 2)),
                },
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            return self._ccmixter_rows(response.json())

        search_results = await asyncio.gather(
            *(search_license(code) for code in ("by", "sa", "pd")),
            return_exceptions=True,
        )
        candidates: dict[str, dict[str, Any]] = {}
        for outcome in search_results:
            if isinstance(outcome, BaseException):
                log.debug("ccMixter query failed (%s).", type(outcome).__name__)
                continue
            for row in outcome:
                upload_id = str(row.get("upload_id") or row.get("id") or "").strip()
                if re.fullmatch(r"\d{1,12}", upload_id):
                    candidates.setdefault(upload_id, row)
        if not candidates:
            return []

        semaphore = asyncio.Semaphore(3)

        async def inspect_upload(upload_id: str, search_row: dict[str, Any]) -> dict[str, Any] | None:
            async with semaphore:
                try:
                    info_response, files_response = await asyncio.gather(
                        self.http.get(
                            base,
                            params={"f": "json", "dataview": "info", "ids": upload_id},
                            headers={"Accept": "application/json"},
                        ),
                        self.http.get(
                            base,
                            params={"f": "json", "dataview": "files", "ids": upload_id},
                            headers={"Accept": "application/json"},
                        ),
                    )
                    info_response.raise_for_status()
                    files_response.raise_for_status()
                    info_rows = self._ccmixter_rows(info_response.json())
                    file_rows = self._ccmixter_rows(files_response.json())
                    info = info_rows[0] if info_rows else search_row
                    licensed = self._ccmixter_license(info)
                    if not licensed:
                        return None
                    license_label, license_url = licensed

                    title = _clean(
                        info.get("file_name") or info.get("upload_name")
                        or search_row.get("file_name") or search_row.get("upload_name")
                        or f"ccMixter {upload_id}",
                        160,
                    )
                    creator = _clean(
                        info.get("user_real_name") or info.get("user_name")
                        or search_row.get("user_real_name") or search_row.get("user_name")
                        or "Autor no indicado",
                        100,
                    )
                    artist_slug = str(info.get("user_name") or search_row.get("user_name") or "").strip()
                    item_url = str(info.get("file_page_url") or search_row.get("file_page_url") or "").strip()
                    if not item_url and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", artist_slug):
                        item_url = f"https://ccmixter.org/files/{quote(artist_slug, safe='')}/{upload_id}"

                    for file_item in file_rows:
                        audio_url = str(file_item.get("download_url") or file_item.get("file_url") or "").strip()
                        extension = Path(urlparse(audio_url).path).suffix.casefold()
                        if not _allowed_audio_url(audio_url) or extension not in AUDIO_EXTENSIONS:
                            continue
                        try:
                            size = int(file_item.get("filesize") or file_item.get("file_rawsize") or 0)
                        except (TypeError, ValueError, OverflowError):
                            size = 0
                        if size > MAX_TRACK_BYTES:
                            continue
                        return {
                            "identifier": upload_id,
                            "title": title,
                            "creator": creator,
                            "license": license_label,
                            "license_url": license_url,
                            "item_url": item_url,
                            "url": audio_url,
                            "source": "ccMixter",
                        }
                    return None
                except (httpx.HTTPError, ValueError, TypeError, KeyError, OverflowError):
                    log.debug("ccMixter upload validation failed for %s.", upload_id, exc_info=True)
                    return None

        inspected = await asyncio.gather(
            *(
                inspect_upload(upload_id, row)
                for upload_id, row in list(candidates.items())[:max(12, limit * 3)]
            ),
            return_exceptions=True,
        )
        results: list[dict[str, Any]] = []
        for item in inspected:
            if isinstance(item, dict):
                results.append(item)
                if len(results) >= limit:
                    break
        return results

    async def search_audius(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search public Audius tracks and stream only those with explicit CC/PD metadata."""
        text = " ".join(str(query or "").split())[:140]
        if not text:
            return []
        response = await self.http.get(
            "https://api.audius.co/v1/tracks/search",
            params={"query": text, "limit": max(5, min(20, limit * 3)), "app_name": "StellaAnarchy"},
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        records = (payload.get("data") or []) if isinstance(payload, dict) else []
        if not isinstance(records, list):
            return []

        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for item in records:
            if not isinstance(item, dict):
                continue
            track_id = str(item.get("id") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", track_id) or track_id in seen_ids:
                continue
            is_streamable = item.get("is_streamable", item.get("isStreamable"))
            if is_streamable not in (True, 1, "true", "True", "1"):
                continue
            licensed = _audius_license_label(item.get("license") or item.get("license_type") or "")
            if not licensed:
                continue
            license_label, license_url = licensed
            try:
                duration = float(item.get("duration") or 0)
            except (TypeError, ValueError, OverflowError):
                duration = 0
            if duration > MAX_TRACK_SECONDS:
                continue

            user = item.get("user") or {}
            if not isinstance(user, dict):
                user = {}
            creator = _clean(
                user.get("name") or user.get("handle") or user.get("username")
                or item.get("artist_name") or "Artista de Audius",
                100,
            )
            title = _clean(item.get("title") or "Pista de Audius", 160)
            permalink = str(item.get("permalink") or "").strip()
            if not permalink.startswith("https://audius.co/"):
                permalink = f"https://audius.co/tracks/{quote(track_id, safe='')}"
            stream_url = (
                f"https://api.audius.co/v1/tracks/{quote(track_id, safe='')}/stream"
                "?app_name=StellaAnarchy"
            )
            results.append({
                "identifier": track_id,
                "title": title,
                "creator": creator,
                "license": license_label,
                "license_url": license_url,
                "item_url": permalink,
                "url": stream_url,
                "source": "Audius",
            })
            seen_ids.add(track_id)
            if len(results) >= limit:
                break
        return results

    async def search_jamendo(self, query: str, limit: int = 4) -> list[dict[str, Any]]:
        client_id = os.getenv("JAMENDO_CLIENT_ID", "").strip()
        if not client_id:
            return []
        response = await self.http.get(
            "https://api.jamendo.com/v3.0/tracks/",
            params={
                "client_id": client_id, "format": "json", "limit": max(4, min(20, limit * 3)),
                "namesearch": str(query)[:100], "audiodownload_allowed": "true",
                "include": "licenses", "audioformat": "mp32",
            },
        )
        response.raise_for_status()
        results = []
        for item in response.json().get("results", []):
            if item.get("audiodownload_allowed") not in (True, "true", "True"):
                continue
            license_url = str(item.get("license_ccurl") or "")
            label = _license_label(license_url)
            audio_url = str(item.get("audiodownload") or "")
            if not label or not _allowed_audio_url(audio_url):
                continue
            track_id = str(item.get("id") or "")
            results.append({
                "identifier": track_id,
                "title": _clean(item.get("name") or "Pista de Jamendo"),
                "creator": _clean(item.get("artist_name") or "Autor no indicado"),
                "license": label,
                "license_url": license_url,
                "item_url": f"https://www.jamendo.com/track/{quote(track_id, safe='')}",
                "url": audio_url,
                "source": "Jamendo",
            })
            if len(results) >= limit:
                break
        return results

    async def search_music(self, room_id: str, query: str) -> tuple[bool, str]:
        text = " ".join(str(query or "").split())[:180]
        if not text:
            return False, "Uso: !dj buscar <artista, canción, género o ambiente>."
        lookup = text
        if os.getenv("MUSIC_AI_SEARCH", "1").strip().casefold() not in {"0", "false", "off"}:
            try:
                ai = getattr(self, "ai", None)
                if ai:
                    lookup = await ai.optimize_music_query(text) or text
            except Exception:
                log.info("Music query optimization skipped.")

        providers = [
            ("Internet Archive", self.search_archive, lookup, 5),
            ("Wikimedia Commons", self.search_wikimedia, lookup, 5),
            ("Openverse", self.search_openverse, lookup, 5),
            ("ccMixter", self.search_ccmixter, lookup, 5),
            ("Audius", self.search_audius, lookup, 5),
        ]
        if os.getenv("JAMENDO_CLIENT_ID", "").strip():
            providers.append(("Jamendo", self.search_jamendo, lookup, 4))

        outcomes = await asyncio.gather(
            *(searcher(term, limit) for _, searcher, term, limit in providers),
            return_exceptions=True,
        )
        results: list[dict[str, Any]] = []
        errors = 0
        for (source_name, _, _, _), outcome in zip(providers, outcomes):
            if isinstance(outcome, BaseException):
                errors += 1
                log.warning("Music catalog %s failed (%s).", source_name, type(outcome).__name__)
                continue
            if isinstance(outcome, list):
                results.extend(item for item in outcome if isinstance(item, dict))

        unique: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for item in results:
            url = str(item.get("url") or "").strip()
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique.append(item)
        unique = unique[:8]
        if not unique:
            if errors == len(providers):
                return False, "Los catálogos de música no están disponibles en este momento. Intenta nuevamente más tarde."
            return False, "No encontré pistas con licencia abierta que pudiera validar. Prueba con el nombre de un artista, canción o un género más concreto."

        self.search_results[room_id] = {
            "items": unique,
            "query": text,
            "expires": time.monotonic() + 600,
        }
        lines = [f"🎧 RESULTADOS PARA: {text}"]
        for index, item in enumerate(unique, 1):
            lines.append(
                f"{index}. {_clean(item.get('title') or 'Pista sin título', 120)} — "
                f"{_clean(item.get('creator') or 'Autor no indicado', 80)}\n"
                f"{_clean(item.get('source') or 'Catálogo público', 50)} · "
                f"{_clean(item.get('license') or 'Licencia abierta', 80)}"
            )
        lines.append("Elige una pista con los botones o usa !dj reproducir|encolar <número>.")
        return True, "\n".join(lines)

    async def send_search_page(
        self, room_id: str, page: int = 1, *, reply_id: str | None = None
    ) -> bool:
        """Send one paginated music result page with native Anarchy selection buttons."""
        saved = self.search_results.get(room_id) or {}
        if saved.get("expires", 0) <= time.monotonic():
            self.search_results.pop(room_id, None)
            await self.platform.send_message(
                room_id, "Los resultados caducaron. Usa !dj buscar <tema> nuevamente.",
                reply_id=reply_id,
            )
            return False
        items = saved.get("items") or []
        page_size = 4
        page_count = max(1, (len(items) + page_size - 1) // page_size)
        page = max(1, min(int(page), page_count))
        start = (page - 1) * page_size
        selected = items[start:start + page_size]
        buttons: list[dict[str, str]] = []
        for offset, item in enumerate(selected):
            number = start + offset + 1
            title = _clean(item.get("title") or "Pista", 30)
            buttons.append({"id": f"play_{number}", "label": f"▶ {number}. {title}"[:40]})
        if page < page_count:
            buttons.append({"id": f"page_{page + 1}", "label": "Siguientes pistas →"})
        elif page > 1:
            buttons.append({"id": f"page_{page - 1}", "label": "← Pistas anteriores"})

        lines = [
            f"🎧 {_clean(saved.get('query') or 'BÚSQUEDA MUSICAL', 120)}",
            f"Página {page}/{page_count} · {len(items)} pistas · Pulsa una pista para reproducirla.",
        ]
        for offset, item in enumerate(selected):
            number = start + offset + 1
            title = _clean(item.get("title") or "Pista sin título", 120)
            creator = _clean(item.get("creator") or "Autor no indicado", 70)
            source = _clean(item.get("source") or "Catálogo público", 40)
            license_name = _clean(item.get("license") or "Licencia abierta", 70)
            lines.append(f"{number}. {title} — {creator}\n{source} · {license_name}")
        lines.append("También puedes usar !dj encolar <número> para añadir sin interrumpir la pista actual.")
        response = await self.platform.send_message(
            room_id,
            "\n".join(lines)[:1900],
            reply_id=reply_id,
            buttons=buttons,
            return_message_id=True,
        )
        if not isinstance(response, str) or not response:
            await self.platform.send_message(
                room_id,
                "No pude activar los botones. Usa !dj reproducir <número> o !dj encolar <número>.",
                reply_id=reply_id,
            )
            return False

        now = time.monotonic()
        for key, session in list(self.interaction_sessions.items()):
            if session.get("expires", 0) <= now or key[0] == room_id:
                self.interaction_sessions.pop(key, None)
        self.interaction_sessions[(room_id, response)] = {
            "items": items,
            "page": page,
            "expires": saved["expires"],
        }
        return True

    async def search_music_interactive(
        self, room_id: str, query: str, *, reply_id: str | None = None
    ) -> bool:
        ok, message = await self.search_music(room_id, query)
        if not ok:
            await self.platform.send_message(room_id, message, reply_id=reply_id)
            return False
        return await self.send_search_page(room_id, 1, reply_id=reply_id)

    async def handle_interaction(self, event: dict[str, Any]) -> bool:
        """Consume Anarchy button presses once, then remove the buttons from that message."""
        room_id = str(event.get("threadId") or event.get("thread_id") or "").strip()
        message_id = str(event.get("messageId") or event.get("message_id") or "").strip()
        button_id = str(event.get("buttonId") or "").strip()
        key = (room_id, message_id)
        session = self.interaction_sessions.pop(key, None)
        if not room_id or not message_id or not session:
            return False
        if session.get("expires", 0) <= time.monotonic():
            await self.platform.edit_message(room_id, message_id, remove_buttons=True)
            return False

        await self.platform.edit_message(room_id, message_id, remove_buttons=True)
        if button_id.startswith("page_"):
            try:
                next_page = int(button_id.removeprefix("page_"))
            except ValueError:
                return False
            saved = self.search_results.get(room_id) or {}
            if saved.get("expires", 0) <= time.monotonic():
                return False
            return await self.send_search_page(room_id, next_page, reply_id=message_id)

        match = re.fullmatch(r"play_(\d{1,2})", button_id)
        if not match:
            return False
        number = int(match.group(1))
        items = session.get("items") or []
        if not 1 <= number <= len(items):
            return False
        track = items[number - 1]
        if not _allowed_audio_url(str(track.get("url") or "")) or not _is_open_license(str(track.get("license") or "")):
            await self.platform.send_message(
                room_id, "La pista seleccionada ya no supera la validación de fuente o licencia.",
                reply_id=message_id,
            )
            return False
        ok, response = await self._queue_track(room_id, track, enqueue=False)
        await self.platform.send_message(room_id, response, reply_id=message_id)
        return ok

    async def _start_player(self, room_id: str, state: dict[str, Any]) -> None:
        task = state.get("music_task")
        if task is None or task.done():
            state["music_task"] = asyncio.create_task(self._music_worker(room_id, state))

    async def _queue_track(self, room_id: str, track: dict[str, Any], *, enqueue: bool) -> tuple[bool, str]:
        ok, message = await self.ensure_room(room_id)
        if not ok:
            return False, message
        state = self.rooms[room_id]
        if len(state["music_queue"]) >= MAX_MUSIC_QUEUE:
            return False, f"La cola musical ya alcanzó {MAX_MUSIC_QUEUE} pistas."
        if not enqueue:
            state["music_queue"].clear()
            state["music_queue"].append(track)
            if state.get("music_current"):
                state["music_skip"].set()
            else:
                state["music_skip"].clear()
            state["music_paused"].set()
        else:
            state["music_queue"].append(track)
        await self._start_player(room_id, state)
        position = len(state["music_queue"]) + (1 if state.get("music_current") else 0)
        return True, f"«{track['title']}» {'añadida a la cola (posición ' + str(position) + ')' if enqueue else 'enviada al reproductor'}."

    async def enqueue_track(self, room_id: str, number: int, *, enqueue: bool) -> tuple[bool, str]:
        saved = self.search_results.get(room_id) or {}
        if saved.get("expires", 0) <= time.monotonic():
            self.search_results.pop(room_id, None)
            return False, "Los resultados caducaron. Usa !dj buscar <tema> de nuevo."
        items = saved.get("items") or []
        if number < 1 or number > len(items):
            return False, "Ese número no corresponde a los resultados actuales."
        track = items[number - 1]
        if not _allowed_audio_url(str(track.get("url") or "")) or not _is_open_license(str(track.get("license") or "")):
            return False, "La pista no supera la validación de fuente o licencia."
        return await self._queue_track(room_id, track, enqueue=enqueue)

    async def enqueue_saved_track(self, room_id: str, track: dict[str, Any], *, enqueue: bool) -> tuple[bool, str]:
        if not isinstance(track, dict) or not _allowed_audio_url(str(track.get("url") or "")):
            return False, "El favorito no contiene una fuente musical autorizada."
        if not _license_label(str(track.get("license") or "")):
            return False, "El favorito no conserva una licencia musical compatible."
        return await self._queue_track(room_id, dict(track), enqueue=enqueue)

    async def enqueue_local_track(self, room_id: str, filename: str, *, enqueue: bool) -> tuple[bool, str]:
        path = resolve_local_audio(filename)
        if path is None:
            return False, "No encontré ese archivo local válido. Revisa !dj archivos y el directorio DJ_MUSIC_DIR."
        track = {
            "title": path.stem[:160], "creator": "Archivo local",
            "license": "Archivo local", "source": "Archivo local",
            "item_url": "", "local_path": str(path),
        }
        return await self._queue_track(room_id, track, enqueue=enqueue)

    @staticmethod
    async def _wait_music_gate(state: dict[str, Any]) -> None:
        """Keep music paused while TTS owns the shared LiveKit output track."""
        paused = state.get("music_paused")
        speech_done = state.get("speech_done")
        while True:
            if paused is not None:
                await paused.wait()
            if speech_done is not None:
                await speech_done.wait()
            if (paused is None or paused.is_set()) and (speech_done is None or speech_done.is_set()):
                return

    async def _play_audio_with_recovery(
        self, room_id: str, state: dict[str, Any], data: bytes, track: dict[str, Any]
    ) -> None:
        for attempt in range(2):
            if attempt:
                ready = state.get("connection_ready")
                if ready is not None:
                    await asyncio.wait_for(ready.wait(), timeout=12.0)
                if self.rooms.get(room_id) is not state or not state.get("connected"):
                    raise RuntimeError("La llamada terminó durante la recuperación del audio.")
                await asyncio.sleep(0.5)
            try:
                await self._play_audio_bytes(
                    state["music_source"], data, state["music_volume"],
                    room_id=room_id, skip_event=state["music_skip"],
                    pause_event=lambda: self._wait_music_gate(state),
                    fade_ms=int(state.get("transition_ms", 500)),
                    duck_state=state,
                    capture_lock=state.get("capture_lock"),
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if (
                    attempt
                    or state["music_skip"].is_set()
                    or not self._is_transient_livekit_error(exc)
                ):
                    raise
                log.warning(
                    "LiveKit playback interrupted for room %s; retrying track once (%s).",
                    room_id[:80], type(exc).__name__,
                )
                await self._send(
                    room_id,
                    f"🔄 Se interrumpió el audio de «{_clean(track.get('title') or 'la pista')}»; "
                    "estoy reintentando la pista desde el principio una vez.",
                )

    async def _music_worker(self, room_id: str, state: dict[str, Any]) -> None:
        while self.rooms.get(room_id) is state and state.get("connected"):
            if not state["music_queue"]:
                state["music_current"] = None
                return
            track = state["music_queue"].popleft()
            state["music_current"] = track
            playback_succeeded = False
            try:
                local_path = track.get("local_path")
                if local_path:
                    local_file = resolve_local_audio(Path(str(local_path)).name)
                    if local_file is None or str(local_file) != str(Path(str(local_path)).resolve()):
                        raise RuntimeError("El archivo local ya no está disponible o salió del directorio permitido.")
                    data = await asyncio.to_thread(local_file.read_bytes)
                else:
                    data = await self._download_audio(str(track.get("url") or ""))
                details = f"Fuente: {track['source']} · Licencia: {track['license']}"
                await self._send(
                    room_id,
                    f"🎧 Ahora suena: {_clean(track.get('title') or 'Pista')} — "
                    f"{_clean(track.get('creator') or 'Autor no indicado')}\n" + details,
                )
                await self._play_audio_with_recovery(room_id, state, data, track)
                playback_succeeded = not state["music_skip"].is_set()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning(
                    "Music playback failed (%s): %s",
                    type(exc).__name__,
                    str(exc)[:280] or "sin detalle",
                )
                await self._send(room_id, f"No pude reproducir «{track.get('title', 'la pista')}». Puedes elegir otra de la búsqueda.")
            finally:
                skipped = state["music_skip"].is_set()
                state["music_skip"].clear()
                if playback_succeeded and not skipped and state.get("repeat_mode") == "one":
                    state["music_queue"].appendleft(track)
                elif playback_succeeded and not skipped and state.get("repeat_mode") == "queue":
                    state["music_queue"].append(track)
                state["music_current"] = None

    async def pause_music(self, room_id: str, paused: bool) -> str:
        state = self.rooms.get(room_id)
        if not state:
            return "Stella no está conectada a una llamada. Usa !join primero."
        if paused:
            state["music_paused"].clear()
            return "⏸️ Música pausada."
        state["music_paused"].set()
        return "▶️ Reproducción reanudada."

    async def skip_music(self, room_id: str) -> str:
        state = self.rooms.get(room_id)
        if not state:
            return "Stella no está conectada a una llamada. Usa !join primero."
        state["music_skip"].set()
        state["music_paused"].set()
        return "⏭️ Saltando la pista actual."

    async def stop_music(self, room_id: str) -> str:
        state = self.rooms.get(room_id)
        if not state:
            return "No hay una llamada de voz activa."
        state["music_queue"].clear()
        state["music_skip"].set()
        state["music_paused"].set()
        source = state.get("music_source")
        try:
            if source and callable(getattr(source, "clear_queue", None)):
                source.clear_queue()
        except Exception:
            log.exception("Could not clear LiveKit music buffer")
        return "⏹️ Música detenida. Stella permanece dentro de la llamada."

    async def set_music_volume(self, room_id: str, target: int) -> str:
        state = self.rooms.get(room_id)
        if not state:
            return "Stella no está conectada a una llamada. Usa !join primero."
        if not 0 <= target <= 100:
            return "El volumen debe estar entre 0 y 100."
        state["music_volume"] = target / 100.0
        return f"Volumen musical ajustado a {target}%."

    async def stop_all_speech(self) -> None:
        """Stop queued/current TTS in every connected room without disabling its settings."""
        current = asyncio.current_task()
        for room_id, state in list(self.rooms.items()):
            queue = state.get("speech_queue")
            if queue is not None:
                while not queue.empty():
                    try:
                        queue.get_nowait()
                        queue.task_done()
                    except asyncio.QueueEmpty:
                        break
            task = state.get("speech_task")
            if task is not None and task is not current and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    log.exception("Could not stop a speech worker during global silence")
            if state.get("speech_task") is task:
                state["speech_task"] = None

    async def enable_reading(self, room_id: str, enabled: bool) -> tuple[bool, str]:
        if enabled:
            ok, message = await self.ensure_room(room_id)
            if not ok:
                return False, message
            state = self.rooms[room_id]
            state["tts_enabled"] = True
            await self._start_speech_worker(room_id, state)
            return True, "Lectura en voz alta activada. Leeré los mensajes nuevos de esta sala."
        state = self.rooms.get(room_id)
        if state:
            state["tts_enabled"] = False
            queue = state.get("speech_queue")
            if queue:
                while not queue.empty():
                    try:
                        queue.get_nowait()
                        queue.task_done()
                    except asyncio.QueueEmpty:
                        break
        return True, "Lectura en voz alta desactivada."

    async def enqueue_speech(self, room_id: str, content: str) -> None:
        state = self.rooms.get(room_id)
        if not state or not state.get("connected") or not state.get("tts_enabled"):
            return
        text = self._clean_speech(content, read_links=bool(state.get('tts_read_links')), read_mentions=bool(state.get('tts_read_mentions')))
        if not text:
            return
        queue = state["speech_queue"]
        if queue.full():
            try:
                queue.get_nowait()
                queue.task_done()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(text[:650])
        except asyncio.QueueFull:
            return
        await self._start_speech_worker(room_id, state)

    @staticmethod
    def _clean_speech(value: str, *, read_links: bool = False, read_mentions: bool = False) -> str:
        text = str(value or "")
        if read_links:
            def describe_link(match: re.Match) -> str:
                raw = match.group(0).rstrip(".,;:!?)]}")
                host = (urlparse(raw if "://" in raw else "https://" + raw).hostname or "").removeprefix("www.")
                return " enlace a " + host if host else " enlace "
            text = re.sub(r"https?://\S+|www\.\S+", describe_link, text, flags=re.I)
        else:
            text = re.sub(r"https?://\S+|www\.\S+", " enlace ", text, flags=re.I)
        if read_mentions:
            text = re.sub(r"(?<!\w)@([\w.-]{1,64})", r" \1 ", text)
        else:
            text = re.sub(r"(?<!\w)@[\w.-]{1,64}", " usuario ", text)
        text = re.sub(r"[*_~`>#|]", " ", text)
        return re.sub(r"\s+", " ", text).strip()[:650]

    async def _start_speech_worker(self, room_id: str, state: dict[str, Any]) -> None:
        task = state.get("speech_task")
        if task is None or task.done():
            state["speech_task"] = asyncio.create_task(self._speech_worker(room_id, state))

    async def _speech_worker(self, room_id: str, state: dict[str, Any]) -> None:
        if not self._load_audio(tts=True):
            await self._send(room_id, "La lectura necesita Edge TTS, PyAV y LiveKit; una dependencia no está disponible.")
            return
        queue = state["speech_queue"]
        while self.rooms.get(room_id) is state and state.get("connected") and state.get("tts_enabled"):
            try:
                text = await asyncio.wait_for(queue.get(), timeout=20)
            except asyncio.TimeoutError:
                return
            try:
                with tempfile.NamedTemporaryFile(prefix="stella-tts-", suffix=".mp3", delete=False) as temporary:
                    path = temporary.name
                try:
                    communicator = self._edge_tts.Communicate(
                        text,
                        state["tts_voice"],
                        rate=state["tts_rate"],
                    )
                    await communicator.save(path)
                    data = await asyncio.to_thread(lambda: open(path, "rb").read())
                    active = state.get("tts_active")
                    speech_done = state.get("speech_done")
                    if active is not None:
                        active.set()
                    if speech_done is not None:
                        speech_done.clear()
                    try:
                        await self._play_audio_bytes(
                            state["speech_source"], data, state["tts_volume"], room_id=room_id,
                            capture_lock=state.get("capture_lock"),
                        )
                    finally:
                        if active is not None:
                            active.clear()
                        if speech_done is not None:
                            speech_done.set()
                finally:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("TTS playback failed (%s): %s", type(exc).__name__, str(exc)[:280] or "sin detalle")
            finally:
                queue.task_done()

    async def configure_tts(self, room_id: str, setting: str, value: str) -> str:
        state = self.rooms.get(room_id)
        if not state:
            return "Conecta primero a una llamada con !leer o !join."
        setting, value = setting.casefold().strip(), value.strip().casefold()
        if setting in {"voz", "voice"}:
            if value not in TTS_VOICES:
                return "Voces: " + ", ".join(TTS_VOICES)
            state["tts_voice"] = TTS_VOICES[value]
            return f"Voz cambiada a {value}."
        if setting in {"velocidad", "rate"}:
            try:
                rate = int(value.replace("%", "").replace("+", ""))
            except ValueError:
                return "La velocidad debe estar entre -50% y +100%."
            if not -50 <= rate <= 100:
                return "La velocidad debe estar entre -50% y +100%."
            state["tts_rate"] = f"{rate:+d}%"
            return f"Velocidad ajustada a {state['tts_rate']}."
        if setting in {"volumen", "volume"}:
            current = int(round(float(state.get("tts_volume", 0.75)) * 100))
            previous = int(round(float(state.get("last_tts_volume", 0.75)) * 100))
            if value in {"mute", "silencio"}:
                if current:
                    state["last_tts_volume"] = current / 100.0
                target = 0
            elif value in {"restaurar", "restore"}:
                target = previous or 75
            elif value in {"subir", "up"}:
                target = min(100, current + 10)
            elif value in {"bajar", "down"}:
                target = max(0, current - 10)
            else:
                try:
                    target = current + int(value) if value.startswith(("+", "-")) else int(value)
                except ValueError:
                    return "Usa 0-100, +10, -10, subir, bajar, mute o restaurar."
            if not 0 <= target <= 100:
                return "El volumen de lectura debe quedar entre 0 y 100."
            if target:
                state["last_tts_volume"] = target / 100.0
            state["tts_volume"] = target / 100.0
            return f"Volumen de lectura ajustado a {target}%."
        if setting in {"enlaces", "links", "menciones", "mentions"}:
            if value not in {"on", "off", "si", "sí", "no", "true", "false", "1", "0", "activar", "desactivar"}:
                return "Usa on u off."
            enabled = value in {"on", "si", "sí", "true", "1", "activar"}
            key = "tts_read_links" if setting in {"enlaces", "links"} else "tts_read_mentions"
            state[key] = enabled
            if self.store:
                self.store.set_setting(room_id, key, "1" if enabled else "0")
            return f"Lectura de {'enlaces' if key == 'tts_read_links' else 'menciones'} {'activada' if enabled else 'desactivada'}."
        return "Uso: !tts voz <nombre>, velocidad <-50 a 100>, volumen <valor>, enlaces on|off o menciones on|off."


    async def handle_command(self, room_id: str, command: str, args: str) -> tuple[bool, str]:
        """Handle voice/music commands. Returns (handled, user-facing result)."""
        command = command.casefold().strip()
        if command in {"join", "unirsala"}:
            ok, message = await self.ensure_room(room_id)
            return True, ("🎙️ " if ok else "Voz: ") + message
        if command in {"salirsala", "leavevoice"}:
            if room_id not in self.rooms:
                return True, "Stella no está conectada a una llamada."
            await self.leave_room(room_id, request_leave=True)
            return True, "Stella salió de la llamada y detuvo todos los canales de audio."
        if command in {"leer", "noleer"}:
            enabled = command == "leer"
            if args.strip().casefold() in {"off", "desactivar", "no"}:
                enabled = False
            if args.strip().casefold() in {"on", "activar", "si", "sí"}:
                enabled = True
            ok, message = await self.enable_reading(room_id, enabled)
            return True, message
        if command == "voz":
            state = self.rooms.get(room_id)
            if not state:
                return True, "Stella no está conectada a una llamada. Usa !join."
            return True, (
                "🎙️ ESTADO DE VOZ Y LIVEKIT\n"
                f"Conexión: {state.get('connection_state', 'desconocida')} · "
                f"{'conectada' if self._room_is_connected(state.get('room'), state) else 'sin conexión'}\n"
                f"Pista de audio: {'publicada' if state.get('audio_publication_sid') else 'no publicada'}\n"
                f"Lectura: {'activada' if state['tts_enabled'] else 'desactivada'}\n"
                f"Cola TTS: {state['speech_queue'].qsize()}\n"
                f"Voz: {state['tts_voice']} · Velocidad: {state['tts_rate']}\n"
                f"Volumen TTS: {round(state['tts_volume'] * 100)}%"
            )
        if command == "tts":
            parts = args.split(maxsplit=1)
            if not parts or parts[0].casefold() in {"estado", "ajustes", "config"}:
                state = self.rooms.get(room_id)
                if not state:
                    return True, "Conecta primero con !join o activa !leer."
                return True, (
                    "🔊 AJUSTES TTS\n"
                    f"Voz: {state['tts_voice']}\nVelocidad: {state['tts_rate']}\n"
                    f"Volumen: {round(state['tts_volume'] * 100)}%\n"
                    f"Lectura automática: {'sí' if state['tts_enabled'] else 'no'}\n"
                    "Usos: !tts voces | !tts voz <nombre> | !tts velocidad <-50 a 100> | !tts volumen <0 a 100>"
                )
            if parts[0].casefold() == "voces":
                return True, "Voces disponibles: " + ", ".join(TTS_VOICES)
            if parts[0].casefold() == "probar":
                ok, message = await self.ensure_room(room_id)
                if not ok:
                    return True, message
                state = self.rooms[room_id]
                if not self._load_audio(tts=True):
                    return True, "No están disponibles Edge TTS, PyAV o LiveKit."
                state["tts_enabled"] = True
                await self._start_speech_worker(room_id, state)
                await self.enqueue_speech(room_id, parts[1] if len(parts) > 1 else "Prueba de voz de Stella.")
                return True, "Envié el texto de prueba a la cola y activé la lectura en esta sala."
            if len(parts) < 2:
                return True, "Uso: !tts voz|velocidad|volumen <valor>."
            return True, await self.configure_tts(room_id, parts[0], parts[1])
        if command in {"dj", "reproducir"}:
            args = (("reproducir " + args) if command == "reproducir" else args).strip()
            return True, await self._dj_command(room_id, args)
        return False, ""

    @staticmethod
    def youtube_search_message(query: str) -> str:
        """Build a first-party YouTube search/watch link; never extract or relay its media."""
        text = " ".join(str(query or "").split())[:180]
        if not text:
            return "Uso: !dj youtube <artista, canción o enlace de YouTube>."

        parsed = urlparse(text)
        host = (parsed.hostname or "").casefold().rstrip(".")
        video_id = ""
        try:
            port = parsed.port
        except ValueError:
            port = -1
        if (
            parsed.scheme in {"https", "http"}
            and host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
            and not parsed.username and not parsed.password
            and port in {None, 80, 443}
        ):
            if host == "youtu.be":
                candidate = parsed.path.strip("/").split("/", 1)[0]
                if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
                    video_id = candidate
            else:
                match = re.search(r"(?:^|&)v=([A-Za-z0-9_-]{11})(?:&|$)", parsed.query)
                if match:
                    video_id = match.group(1)
                else:
                    parts = [part for part in parsed.path.split("/") if part]
                    if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"}:
                        if re.fullmatch(r"[A-Za-z0-9_-]{11}", parts[1]):
                            video_id = parts[1]

        if video_id:
            target = f"https://www.youtube.com/watch?v={quote(video_id, safe='')}"
            heading = "VÍDEO EN YOUTUBE"
        else:
            target = "https://www.youtube.com/results?search_query=" + quote(text, safe="")
            heading = "BÚSQUEDA EN YOUTUBE"
        return (
            f"▶️ {heading}\n{target}\n"
            "La reproducción ocurre en YouTube. Stella no descarga, separa ni retransmite "
            "el audio de YouTube a LiveKit.\n"
            "Términos de YouTube: https://www.youtube.com/t/terms"
        )

    async def _dj_command(self, room_id: str, raw_args: str) -> str:
        parts = raw_args.split(maxsplit=1)
        action = parts[0].casefold() if parts else "ayuda"
        rest = parts[1].strip() if len(parts) > 1 else ""
        if action in {"ayuda", "help", "comandos"}:
            return (
                "🎧 DJ DE STELLA\n"
                "!dj buscar <tema> · !dj ambiente <estilo>\n"
                "!dj reproducir <n> · !dj encolar <n> · !dj resultados · !dj cola · !dj actual\n"
                "!dj pausa · !dj reanudar · !dj saltar · !dj detener · !dj volumen <0-100>\n"
                "!dj favorito lista|guardar <n>|reproducir <n>|quitar <n> [ADMIN]\n"
                "!dj fuentes · !dj youtube <tema> (búsqueda externa) · !dj archivos"
            )
        if action in {"youtube", "yt"}:
            return self.youtube_search_message(rest)
        if action in {"buscar", "search"}:
            if not rest:
                return "Indica qué quieres escuchar. Ejemplo: !dj buscar música ambiental."
            ok, message = await self.search_music(room_id, rest)
            return message
        if action in {"reproducir", "play", "encolar", "agregar"}:
            try:
                number = int(rest)
            except ValueError:
                return "Uso: !dj reproducir <número> o !dj encolar <número>."
            ok, message = await self.enqueue_track(room_id, number, enqueue=action in {"encolar", "agregar"})
            return message
        if action in {"cola", "queue"}:
            state = self.rooms.get(room_id)
            if not state:
                return "Stella no está conectada a una llamada."
            lines = ["🎶 COLA MUSICAL"]
            current = state.get("music_current")
            if current:
                lines.append(f"Reproduciendo: {current.get('title')}")
            else:
                lines.append("No hay una pista reproduciéndose.")
            lines.extend([
                f"Volumen: {round(float(state.get('music_volume', 0.75)) * 100)}%",
                f"Repetición: {state.get('repeat_mode', 'off')}",
                f"Fundido: {int(state.get('transition_ms', 500))} ms",
                f"Atenuación al hablar: {'activada' if state.get('duck_enabled', True) else 'desactivada'}",
            ])
            lines.extend(f"{i}. {track.get('title')} — {track.get('creator')}" for i, track in enumerate(state["music_queue"], 1))
            return "\n".join(lines)
        if action in {"archivos", "archivo", "lista", "locales"}:
            tracks = list_local_audio()
            return "🎼 ARCHIVOS LOCALES\n" + ("\n".join(f"• {name}" for name in tracks) if tracks else "No hay archivos. Colócalos en el directorio DJ_MUSIC_DIR.")
        if action in {"añadir", "agregar", "add", "archivo"}:
            if not rest:
                return "Uso: !dj añadir <archivo>."
            ok, message = await self.enqueue_local_track(room_id, rest, enqueue=True)
            return message
        if action in {"transicion", "transición", "transition"}:
            try:
                milliseconds = int(rest) if rest else int(state_value := self.rooms.get(room_id, {}).get("transition_ms", 500))
            except (TypeError, ValueError):
                milliseconds = -1
            if not 0 <= milliseconds <= 2000:
                return "El fundido debe estar entre 0 y 2000 ms."
            state = self.rooms.get(room_id)
            if not state:
                return "Conecta primero a una llamada con !join."
            state["transition_ms"] = milliseconds
            if self.store:
                self.store.set_setting(room_id, "dj_transition_ms", str(milliseconds))
            return f"🌊 Fundido de entrada/salida ajustado a {milliseconds} ms."
        if action in {"duck", "atenuacion", "atenuación"}:
            state = self.rooms.get(room_id)
            if not state:
                return "Conecta primero a una llamada con !join."
            mode = rest.casefold().strip() or "estado"
            if mode in {"on", "si", "sí", "activar"}:
                state["duck_enabled"] = True
            elif mode in {"off", "no", "desactivar"}:
                state["duck_enabled"] = False
            elif mode not in {"estado", "status"}:
                return "Usa !dj duck on|off|estado."
            if self.store:
                self.store.set_setting(room_id, "dj_duck_enabled", "1" if state["duck_enabled"] else "0")
            return f"🎚️ Atenuación durante la lectura: {'activada' if state['duck_enabled'] else 'desactivada'}."
        if action in {"resultados", "results"}:
            saved = self.search_results.get(room_id) or {}
            if saved.get("expires", 0) <= time.monotonic():
                self.search_results.pop(room_id, None)
                return "Los resultados caducaron. Usa !dj buscar <tema>."
            items = saved.get("items") or []
            return "\n".join([ "🎧 RESULTADOS DE BÚSQUEDA" ] + [f"{i}. {item.get('title')} — {item.get('creator')} · {item.get('source')}" for i, item in enumerate(items, 1)])
        if action in {"ambiente", "mood"}:
            if not rest:
                return "Indica el ambiente que buscas. Ejemplo: !dj ambiente concentración."
            from stella.astra import resolve_music_mood
            ok, message = await self.search_music(room_id, resolve_music_mood(rest))
            return message
        if action in {"fuentes", "sources"}:
            jamendo = "disponible" if os.getenv("JAMENDO_CLIENT_ID", "").strip() else "sin configurar"
            return (
                f"Catálogos para LiveKit: Internet Archive, Wikimedia Commons, Openverse, ccMixter, "
                f"Audius y Jamendo ({jamendo}); solo pistas con licencia abierta verificada. "
                "YouTube está disponible por separado con !dj youtube <tema> y abre el contenido "
                "en YouTube; no se retransmite a LiveKit."
            )
        if action in {"quitar", "remove"}:
            state = self.rooms.get(room_id)
            try:
                index = int(rest) - 1
            except ValueError:
                return "Uso: !dj quitar <posición de la cola>."
            if not state:
                return "Stella no está conectada a una llamada."
            queue = list(state["music_queue"])
            if not 0 <= index < len(queue):
                return "Esa posición no existe en la cola."
            removed = queue.pop(index)
            state["music_queue"] = deque(queue)
            return f"Quité «{removed.get('title')}» de la cola."
        if action in {"mover", "move"}:
            state = self.rooms.get(room_id)
            try:
                source, target = (int(value) - 1 for value in rest.split())
            except (ValueError, TypeError):
                return "Uso: !dj mover <posición actual> <nueva posición>."
            if not state:
                return "Stella no está conectada a una llamada."
            queue = list(state["music_queue"])
            if not (0 <= source < len(queue) and 0 <= target < len(queue)):
                return "Una de esas posiciones no existe."
            track = queue.pop(source)
            queue.insert(target, track)
            state["music_queue"] = deque(queue)
            return "Orden de la cola actualizado."
        if action in {"vaciar", "clear"}:
            state = self.rooms.get(room_id)
            if not state:
                return "Stella no está conectada a una llamada."
            state["music_queue"].clear()
            return "Cola musical vaciada. La pista actual no se interrumpió."
        if action in {"repetir", "repeat"}:
            state = self.rooms.get(room_id)
            if not state:
                return "Stella no está conectada a una llamada."
            mode = rest.casefold().strip()
            if mode not in {"off", "una", "one", "cola", "queue"}:
                return "Uso: !dj repetir off|una|cola."
            state["repeat_mode"] = "one" if mode in {"una", "one"} else "queue" if mode in {"cola", "queue"} else "off"
            label = {"one": "una pista", "queue": "cola completa", "off": "desactivada"}[state["repeat_mode"]]
            return f"Repetición {label}."
        if action in {"probar", "test"}:
            ok, message = await self.ensure_room(room_id)
            if not ok:
                return message
            if not self._load_audio():
                return "No están disponibles las dependencias de audio/LiveKit."
            return "Prueba de audio superada: LiveKit y las dependencias de reproducción están disponibles."
        if action in {"pausa", "pausar", "pause"}:
            return await self.pause_music(room_id, True)
        if action in {"reanudar", "continuar", "resume"}:
            return await self.pause_music(room_id, False)
        if action in {"saltar", "skip"}:
            return await self.skip_music(room_id)
        if action in {"detener", "stop", "parar"}:
            return await self.stop_music(room_id)
        if action in {"volumen", "volume"}:
            try:
                value = int(rest)
            except ValueError:
                return "Uso: !dj volumen <0 a 100>."
            return await self.set_music_volume(room_id, value)
        if action in {"actual", "now"}:
            state = self.rooms.get(room_id)
            current = state.get("music_current") if state else None
            if not state:
                return "Stella no está conectada a una llamada."
            if not current:
                return "No hay una pista sonando."
            return (
                f"🎧 {current.get('title')} — {current.get('creator')}\n"
                f"Volumen: {round(float(state.get('music_volume', 0.75)) * 100)}% · "
                f"Repetición: {state.get('repeat_mode', 'off')}\n"
                f"Fundido: {int(state.get('transition_ms', 500))} ms · "
                f"Atenuación: {'on' if state.get('duck_enabled', True) else 'off'}"
            )
        return (
            "🎧 DJ DE STELLA\n"
            "Usa !dj ayuda para ver todos los controles.\n"
            "!dj buscar <artista, tema o ambiente>\n"
            "!dj reproducir <número> | !dj encolar <número>\n"
            "!dj cola | !dj pausa | !dj reanudar | !dj saltar | !dj detener\n"
            "!dj volumen <0 a 100> | !dj transicion <0-2000> | !dj duck on|off\n"
            "!dj archivos | !dj añadir <archivo> | !reproducir <archivo> | !join | !salirsala"
        )
