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
        self.connect_locks: dict[str, asyncio.Lock] = {}
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

    async def ensure_room(self, room_id: str) -> tuple[bool, str]:
        thread_id = str(room_id or "").strip()
        if not thread_id:
            return False, "No pude identificar el chat que corresponde a la llamada."
        current = self.rooms.get(thread_id)
        if current and current.get("connected"):
            return True, "Stella ya está dentro de la llamada."
        lock = self.connect_locks.setdefault(thread_id, asyncio.Lock())
        async with lock:
            current = self.rooms.get(thread_id)
            if current and current.get("connected"):
                return True, "Stella ya está dentro de la llamada."

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

            joined_api = False
            room = None
            try:
                result = await self.platform.request("POST", f"/live-rooms/{quote(thread_id, safe='')}/join")
                if not result.get("ok"):
                    return False, f"Anarchy rechazó la entrada a la llamada (HTTP {result.get('status')})."
                joined_api = True
                grant = _payload_data(result)
                room_url = str(grant.get("url") or "")
                room_token = str(grant.get("token") or "")
                if not room_url or not room_token:
                    await self.platform.request("POST", f"/live-rooms/{quote(thread_id, safe='')}/leave")
                    return False, "Anarchy no entregó las credenciales temporales de LiveKit."
                room = self._rtc.Room()
                await room.connect(room_url, room_token)

                music_source = self._rtc.AudioSource(SAMPLE_RATE, CHANNELS, queue_size_ms=600)
                music_track = self._rtc.LocalAudioTrack.create_audio_track("stella-music", music_source)
                await room.local_participant.publish_track(
                    music_track,
                    self._rtc.TrackPublishOptions(source=self._rtc.TrackSource.SOURCE_MICROPHONE),
                )

                speech_source = self._rtc.AudioSource(SAMPLE_RATE, CHANNELS, queue_size_ms=300)
                speech_track = self._rtc.LocalAudioTrack.create_audio_track("stella-tts", speech_source)
                await room.local_participant.publish_track(
                    speech_track,
                    self._rtc.TrackPublishOptions(source=self._rtc.TrackSource.SOURCE_MICROPHONE),
                )

                state = {
                    "room": room,
                    "music_source": music_source,
                    "speech_source": speech_source,
                    "connected": True,
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

                def disconnected(*_args):
                    existing = self.rooms.get(thread_id)
                    if existing is state:
                        existing["connected"] = False
                        asyncio.create_task(self.leave_room(thread_id, request_leave=False))

                room.on("disconnected", disconnected)
                self.rooms[thread_id] = state
                return True, "Stella se unió a la llamada y publicó sus canales de música y lectura."
            except Exception as exc:
                log.warning("LiveKit join failed (%s)", type(exc).__name__)
                if room is not None:
                    try:
                        await room.disconnect()
                    except Exception:
                        pass
                if joined_api:
                    await self.platform.request("POST", f"/live-rooms/{quote(thread_id, safe='')}/leave")
                return False, f"No pude conectarme a LiveKit ({type(exc).__name__})."
            finally:
                if not self.rooms.get(thread_id):
                    self.connect_locks.pop(thread_id, None)

    async def leave_room(self, room_id: str, *, request_leave: bool) -> None:
        thread_id = str(room_id)
        state = self.rooms.pop(thread_id, None)
        if state:
            state["connected"] = False
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
        if request_leave:
            try:
                await self.platform.request("POST", f"/live-rooms/{quote(thread_id, safe='')}/leave")
            except Exception:
                log.exception("Could not leave Anarchy voice room")
        self.connect_locks.pop(thread_id, None)

    @staticmethod
    def _frame_samples(frame: Any, volume: float, np: Any):
        samples = frame.to_ndarray().reshape(-1).astype(np.float32, copy=False)
        if volume != 1.0:
            samples = samples * max(0.0, min(1.0, float(volume)))
        return np.clip(samples, -32768, 32767).astype(np.int16, copy=False)

    async def _capture_pcm(self, source: Any, frame: Any, samples: Any) -> None:
        rtc_frame = self._rtc.AudioFrame.create(SAMPLE_RATE, CHANNELS, int(samples.size))
        destination = self._np.frombuffer(rtc_frame.data, dtype=self._np.int16)
        destination[: samples.size] = samples
        await source.capture_frame(rtc_frame)

    async def _play_audio_bytes(
        self, source: Any, data: bytes, volume: float, *, room_id: str,
        skip_event=None, pause_event=None, fade_ms: int = 0,
        duck_state: dict[str, Any] | None = None,
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
                    await self._capture_pcm(source, frame, samples)
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
        term = " ".join(re.findall(r"[\wáéíóúñü-]+", str(query or ""), flags=re.I)[:8])
        if not term:
            return []
        lucene = f'mediatype:audio AND (title:"{term.replace(chr(34), "")}" OR subject:"{term.replace(chr(34), "")}")'
        response = await self.http.get(
            "https://archive.org/advancedsearch.php",
            params={
                "q": lucene,
                "fl[]": ["identifier"],
                "rows": max(10, min(30, limit * 4)),
                "page": 1,
                "output": "json",
            },
        )
        response.raise_for_status()
        docs = response.json().get("response", {}).get("docs", [])
        results: list[dict[str, Any]] = []
        for doc in docs[:12]:
            identifier = str(doc.get("identifier") or "")
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", identifier):
                continue
            metadata_response = await self.http.get(f"https://archive.org/metadata/{quote(identifier, safe='')}")
            if not metadata_response.is_success:
                continue
            payload = metadata_response.json()
            metadata = payload.get("metadata") or {}
            license_url = str(metadata.get("licenseurl") or metadata.get("license_url") or "")
            license_label = _license_label(license_url)
            if not license_label:
                continue
            candidates = []
            for item in payload.get("files") or []:
                if not isinstance(item, dict):
                    continue
                filename = _safe_filename(str(item.get("name") or ""))
                if not filename or item.get("private") is True:
                    continue
                size = int(item.get("size") or 0) if str(item.get("size") or "").isdigit() else 0
                if size > MAX_TRACK_BYTES:
                    continue
                candidates.append((size or MAX_TRACK_BYTES, filename))
            if not candidates:
                continue
            candidates.sort(key=lambda x: (x[0], x[1]))
            filename = candidates[0][1]
            server = str(payload.get("d1") or "")
            directory = str(payload.get("dir") or "")
            host = (urlparse("https://" + server).hostname or "").casefold()
            safe_parts = [p for p in directory.strip("/").split("/") if p and p not in {".", ".."}]
            if server and host.endswith(".archive.org") and safe_parts and directory.startswith("/"):
                base = "https://" + host + "/" + "/".join(quote(p, safe="") for p in safe_parts)
                audio_url = base + "/" + quote(filename, safe="")
            else:
                audio_url = f"https://archive.org/download/{quote(identifier, safe='')}/{quote(filename, safe='')}"
            if not _allowed_audio_url(audio_url):
                continue
            results.append({
                "identifier": identifier,
                "title": _clean(metadata.get("title") or identifier),
                "creator": _clean(metadata.get("creator") or metadata.get("artist") or "Autor no indicado"),
                "license": license_label,
                "license_url": license_url,
                "item_url": f"https://archive.org/details/{quote(identifier, safe='')}",
                "url": audio_url,
                "source": "Internet Archive",
            })
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
                from stella.ai import StellaAI
                # The main bot may inject its shared AI instance; avoid a new provider client here.
                ai = getattr(self, "ai", None)
                if ai:
                    lookup = await ai.optimize_music_query(text) or text
            except Exception:
                log.info("Music query optimization skipped.")
        results: list[dict[str, Any]] = []
        errors = 0
        try:
            results.extend(await self.search_archive(lookup, limit=5))
        except (httpx.HTTPError, ValueError, RuntimeError) as exc:
            errors += 1
            log.info("Internet Archive search unavailable (%s)", type(exc).__name__)
        try:
            results.extend(await self.search_jamendo(lookup, limit=4))
        except (httpx.HTTPError, ValueError, RuntimeError) as exc:
            errors += 1
            log.info("Jamendo search unavailable (%s)", type(exc).__name__)
        # Preserve each catalog's exact result; do not silently replace a user choice.
        unique = []
        seen = set()
        for item in results:
            key = str(item.get("identifier") or item.get("url") or "")
            if key and key not in seen:
                seen.add(key)
                unique.append(item)
        unique = unique[:8]
        if not unique:
            return False, "No encontré pistas con licencia abierta que pudiera validar. Prueba otro término." if errors < 2 else "Los catálogos de música no están disponibles en este momento."
        self.search_results[room_id] = {"items": unique, "expires": time.monotonic() + 600}
        lines = [f"🎧 RESULTADOS PARA: {text}", "Selecciona una pista con !dj reproducir <número> o añádela con !dj encolar <número>."]
        for index, item in enumerate(unique, 1):
            lines.append(f"{index}. {item['title']} — {item['creator']}\n{item['source']} · {item['license']}")
        return True, "\n".join(lines)

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
        if not _allowed_audio_url(str(track.get("url") or "")) or not _license_label(str(track.get("license") or "")):
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

    async def _music_worker(self, room_id: str, state: dict[str, Any]) -> None:
        while self.rooms.get(room_id) is state and state.get("connected"):
            if not state["music_queue"]:
                state["music_current"] = None
                return
            track = state["music_queue"].popleft()
            state["music_current"] = track
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
                item_url = str(track.get("item_url") or "").strip()
                await self._send(
                    room_id,
                    f"🎧 Ahora suena: {track['title']} — {track['creator']}\n"
                    + details + (f"\n{item_url}" if item_url else ""),
                )
                await self._play_audio_bytes(
                    state["music_source"], data, state["music_volume"],
                    room_id=room_id, skip_event=state["music_skip"],
                    pause_event=state["music_paused"],
                    fade_ms=int(state.get("transition_ms", 500)),
                    duck_state=state,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Music playback failed (%s)", type(exc).__name__)
                await self._send(room_id, f"No pude reproducir «{track.get('title', 'la pista')}». Puedes elegir otra de la búsqueda.")
            finally:
                skipped = state["music_skip"].is_set()
                state["music_skip"].clear()
                if not skipped and state.get("repeat_mode") == "one":
                    state["music_queue"].appendleft(track)
                elif not skipped and state.get("repeat_mode") == "queue":
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
                    if active is not None:
                        active.set()
                    try:
                        await self._play_audio_bytes(
                            state["speech_source"], data, state["tts_volume"], room_id=room_id,
                        )
                    finally:
                        if active is not None:
                            active.clear()
                finally:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("TTS playback failed (%s)", type(exc).__name__)
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
                "🎙️ VOZ ACTIVA\n"
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

    async def _dj_command(self, room_id: str, raw_args: str) -> str:
        parts = raw_args.split(maxsplit=1)
        action = parts[0].casefold() if parts else "ayuda"
        rest = parts[1].strip() if len(parts) > 1 else ""
        if action in {"buscar", "search"}:
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
            from stella.astra import resolve_music_mood
            ok, message = await self.search_music(room_id, resolve_music_mood(rest))
            return message
        if action in {"fuentes", "sources"}:
            jamendo = "disponible" if os.getenv("JAMENDO_CLIENT_ID", "").strip() else "sin configurar"
            return f"Catálogos musicales: Internet Archive (licencia verificada) y Jamendo ({jamendo})."
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
            "!dj buscar <artista, tema o ambiente>\n"
            "!dj reproducir <número> | !dj encolar <número>\n"
            "!dj cola | !dj pausa | !dj reanudar | !dj saltar | !dj detener\n"
            "!dj volumen <0 a 100> | !dj transicion <0-2000> | !dj duck on|off\n"
            "!dj archivos | !dj añadir <archivo> | !reproducir <archivo> | !join | !salirsala"
        )
