"""Append-only, chronological archive of every Anarchy room event in Qdrant."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

log = logging.getLogger("stella.room_archive")

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|"
    r"token|password|contraseña|client_secret|webhook_secret)\s*[:=]\s*([^\s,;]+)"
)
_BEARER_SECRET = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")
_KNOWN_SECRET = re.compile(
    r"\b(?:anb_[A-Za-z0-9_-]{12,}|gsk_[A-Za-z0-9_-]{12,}|"
    r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9_-]{16,}|"
    r"AQ\.[A-Za-z0-9_-]{20,})\b"
)
_JWT_SECRET = re.compile(
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?"
    r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)
_SECRET_FIELD = re.compile(
    r"(?:authorization|headers?|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"token|secret|password|contraseña|client[_-]?secret|webhook[_-]?secret)",
    re.IGNORECASE,
)


def _safe_text(value: Any) -> str:
    """Retain room content while removing credential-shaped values only."""
    text = str(value or "")
    text = _PRIVATE_KEY.sub("[REDACTED_PRIVATE_KEY]", text)
    text = _SECRET_ASSIGNMENT.sub(r"\1=[REDACTED]", text)
    text = _BEARER_SECRET.sub("Bearer [REDACTED]", text)
    text = _KNOWN_SECRET.sub("[REDACTED_SECRET]", text)
    return _JWT_SECRET.sub("[REDACTED_SECRET]", text)


def _safe_snapshot(value: Any, *, depth: int = 0) -> Any:
    """Copy the webhook event for auditability, excluding credential fields."""
    if depth > 12:
        return "[MAX_DEPTH]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if _SECRET_FIELD.search(name):
                continue
            result[name] = _safe_snapshot(item, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [_safe_snapshot(item, depth=depth + 1) for item in value[:500]]
    if isinstance(value, str):
        return _safe_text(value[:20000])
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _safe_text(str(value)[:2000])


def _to_epoch_ms(value: Any, fallback_ms: int) -> int:
    if value is None or value == "":
        return fallback_ms
    if isinstance(value, (int, float)):
        numeric = float(value)
        # Accept Unix seconds, milliseconds, or microseconds.
        if numeric > 10**14:
            return int(numeric / 1000)
        if numeric > 10**11:
            return int(numeric)
        return int(numeric * 1000)
    text = str(value).strip()
    if not text:
        return fallback_ms
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)
    except (TypeError, ValueError, OverflowError):
        return fallback_ms


def _to_epoch_us(value: Any, fallback_us: int) -> int:
    if value is None or value == "":
        return fallback_us
    if isinstance(value, (int, float)):
        numeric = float(value)
        # Accept Unix seconds, milliseconds, microseconds, or nanoseconds.
        if numeric > 10**17:
            return int(numeric / 1000)
        if numeric > 10**14:
            return int(numeric)
        if numeric > 10**11:
            return int(numeric * 1000)
        return int(numeric * 1_000_000)
    text = str(value).strip()
    if not text:
        return fallback_us
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1_000_000)
    except (TypeError, ValueError, OverflowError):
        try:
            return int(float(text) * 1_000_000)
        except (TypeError, ValueError, OverflowError):
            return fallback_us


class RoomArchive:
    """Persistent chronological room ledger; independent from AI personal memory."""

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self.url = os.getenv("QDRANT_URL", "").strip().rstrip("/")
        self.api_key = os.getenv("QDRANT_API_KEY", "").strip()
        self.collection = os.getenv(
            "QDRANT_ROOM_ARCHIVE_COLLECTION", "stella_room_archive"
        ).strip() or "stella_room_archive"
        self.client = client
        self._owns_client = client is None
        self.ready = False
        self._collection_lock = asyncio.Lock()
        self._last_order_key = 0
        self.events_written = 0
        self.write_failures = 0
        self.last_failure_log_at = 0.0
        configured_outbox = os.getenv("ROOM_ARCHIVE_OUTBOX_PATH", "").strip()
        data_dir = Path(os.getenv("DATA_DIR", "./data"))
        self.outbox_path = Path(configured_outbox) if configured_outbox else data_dir / "stella_room_archive_outbox.sqlite3"
        self._outbox_lock = asyncio.Lock()
        self._worker_task: asyncio.Task | None = None
        self._worker_stop = asyncio.Event()
        self._worker_wakeup = asyncio.Event()
        self.events_enqueued = 0
        self.last_success_at = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.api_key and self.collection)

    def _outbox_connection(self) -> sqlite3.Connection:
        self.outbox_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.outbox_path), timeout=10.0)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS room_archive_outbox (
                point_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                last_error TEXT NOT NULL DEFAULT ''
            )
            """
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_room_archive_outbox_retry "
            "ON room_archive_outbox(next_attempt_at, created_at)"
        )
        db.commit()
        return db

    def _outbox_count_sync(self) -> int:
        db = self._outbox_connection()
        try:
            row = db.execute("SELECT COUNT(*) FROM room_archive_outbox").fetchone()
            return int(row[0] if row else 0)
        finally:
            db.close()

    @property
    def pending_count(self) -> int:
        try:
            return self._outbox_count_sync()
        except Exception as exc:
            log.warning("Room archive outbox status failed (%s).", type(exc).__name__)
            return -1

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "qdrant_configured": self.enabled,
            "qdrant_ready": self.ready,
            "pending_events": self.pending_count,
            "events_enqueued": self.events_enqueued,
            "events_written": self.events_written,
            "write_failures": self.write_failures,
            "worker_running": bool(self._worker_task and not self._worker_task.done()),
            "last_success_at": self.last_success_at or None,
        }

    @property
    def status(self) -> str:
        pending = self.pending_count
        backlog = f"; {pending} evento(s) pendientes" if pending else ""
        if not self.enabled:
            return "Qdrant no configurado; eventos protegidos en la cola local" + backlog
        if self.ready:
            return "conectado: archivo cronológico activo, sin caducidad automática" + backlog
        return "configurado: el archivo se inicializará al procesar eventos" + backlog

    def start_worker(self) -> None:
        """Start the retry worker after the event loop is running."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_stop.clear()
            self._worker_task = asyncio.create_task(
                self._worker_loop(), name="stella-room-archive-outbox"
            )
        self._worker_wakeup.set()

    async def _worker_loop(self) -> None:
        while not self._worker_stop.is_set():
            try:
                await self.flush_pending(limit=50)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._report_failure("outbox worker", exc)
            self._worker_wakeup.clear()
            try:
                await asyncio.wait_for(
                    self._worker_wakeup.wait(),
                    timeout=5.0 if self.pending_count else 30.0,
                )
            except asyncio.TimeoutError:
                pass

    def _enqueue_sync(self, point_id: str, payload_json: str) -> None:
        db = self._outbox_connection()
        try:
            db.execute(
                """INSERT INTO room_archive_outbox
                   (point_id,payload_json,attempts,next_attempt_at,created_at,last_error)
                   VALUES(?,?,0,0,?,'')
                   ON CONFLICT(point_id) DO NOTHING""",
                (point_id, payload_json, time.time()),
            )
            db.commit()
        finally:
            db.close()

    def _pending_sync(self, limit: int, force: bool) -> list[tuple[str, str, int]]:
        db = self._outbox_connection()
        try:
            if force:
                rows = db.execute(
                    """SELECT point_id,payload_json,attempts FROM room_archive_outbox
                       ORDER BY created_at ASC LIMIT ?""",
                    (limit,),
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT point_id,payload_json,attempts FROM room_archive_outbox
                       WHERE next_attempt_at<=? ORDER BY created_at ASC LIMIT ?""",
                    (time.time(), limit),
                ).fetchall()
            return [(str(row[0]), str(row[1]), int(row[2])) for row in rows]
        finally:
            db.close()

    def _ack_sync(self, point_id: str) -> None:
        db = self._outbox_connection()
        try:
            db.execute("DELETE FROM room_archive_outbox WHERE point_id=?", (point_id,))
            db.commit()
        finally:
            db.close()

    def _retry_sync(self, point_id: str, attempts: int, error: str) -> None:
        next_attempts = attempts + 1
        delay = min(900, max(5, 2 ** min(next_attempts, 10)))
        db = self._outbox_connection()
        try:
            db.execute(
                """UPDATE room_archive_outbox
                   SET attempts=?, next_attempt_at=?, last_error=?
                   WHERE point_id=?""",
                (next_attempts, time.time() + delay, str(error)[:80], point_id),
            )
            db.commit()
        finally:
            db.close()

    async def enqueue_event(
        self,
        room_id: str,
        event_type: str,
        *,
        event_time: Any = None,
        source_event_id: str | None = None,
        actor_uid: str | None = None,
        actor_name: str | None = None,
        message_id: str | None = None,
        reply_to_message_id: str | None = None,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Durably queue an event locally before acknowledging its webhook."""
        room = str(room_id or "").strip()
        kind = str(event_type or "").strip().casefold()
        if not room or not kind:
            return False
        safe_metadata = _safe_snapshot(metadata or {})
        if not isinstance(safe_metadata, dict):
            safe_metadata = {"snapshot": safe_metadata}
        safe_content = _safe_text(content) if content is not None else None
        safe_source_id = str(source_event_id or "").strip()[:160]
        safe_actor_uid = str(actor_uid or "").strip()[:160]
        safe_actor_name = _safe_text(str(actor_name or ""))[:120]
        safe_message_id = str(message_id or "").strip()[:160]
        safe_reply_id = str(reply_to_message_id or "").strip()[:160]
        identity = self._event_identity(
            room, kind, safe_source_id or None, safe_message_id or None,
            event_time, safe_actor_uid or None, safe_content, safe_metadata,
        )
        point_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"stella-room-archive:{identity}")
        )
        queued = {
            "room_id": room,
            "event_type": kind,
            "event_time": event_time,
            "source_event_id": safe_source_id or None,
            "actor_uid": safe_actor_uid or None,
            "actor_name": safe_actor_name or None,
            "message_id": safe_message_id or None,
            "reply_to_message_id": safe_reply_id or None,
            "content": safe_content,
            "metadata": safe_metadata,
        }
        try:
            encoded = json.dumps(
                queued, ensure_ascii=False, separators=(",", ":"), default=str
            )
            await asyncio.to_thread(self._enqueue_sync, point_id, encoded)
            self.events_enqueued += 1
            self._worker_wakeup.set()
            return True
        except Exception as exc:
            self._report_failure("outbox enqueue", exc)
            return False

    async def flush_pending(self, *, limit: int = 50, force: bool = False) -> int:
        """Deliver queued events to Qdrant; failed writes remain retryable."""
        if not self.enabled:
            return 0
        delivered = 0
        async with self._outbox_lock:
            rows = await asyncio.to_thread(
                self._pending_sync, max(1, min(500, int(limit))), bool(force)
            )
            for point_id, payload_json, attempts in rows:
                try:
                    payload = json.loads(payload_json)
                    if not isinstance(payload, dict):
                        raise ValueError("invalid queued event")
                    saved = await self.record_event(**payload)
                    if not saved:
                        raise RuntimeError("Qdrant write did not confirm success")
                    await asyncio.to_thread(self._ack_sync, point_id)
                    delivered += 1
                    self.last_success_at = time.time()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await asyncio.to_thread(
                        self._retry_sync, point_id, attempts, type(exc).__name__
                    )
            return delivered

    def _headers(self) -> dict[str, str]:
        return {"api-key": self.api_key, "Content-Type": "application/json"}

    def _collection_url(self) -> str:
        return f"{self.url}/collections/{quote(self.collection, safe='')}"

    async def _http(self) -> httpx.AsyncClient:
        if not self.enabled:
            raise RuntimeError("Qdrant room archive is not configured")
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(12.0, connect=4.0),
                follow_redirects=False,
            )
            self._owns_client = True
        return self.client

    async def close(self) -> None:
        self._worker_stop.set()
        self._worker_wakeup.set()
        task = self._worker_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        # Pending rows are already committed to SQLite; do not block shutdown
        # on a remote Qdrant outage. The next process can resume the backlog.
        if self._owns_client and self.client is not None and not self.client.is_closed:
            await self.client.aclose()

    def _report_failure(self, operation: str, exc: Exception) -> None:
        self.write_failures += 1
        now = time.monotonic()
        if now - self.last_failure_log_at < 60:
            return
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        reason = f"HTTP {status}" if status is not None else type(exc).__name__
        log.warning("Room archive %s failed (%s).", operation, reason)
        self.last_failure_log_at = now

    async def ensure_collection(self) -> None:
        if not self.enabled or self.ready:
            return
        async with self._collection_lock:
            if self.ready:
                return
            client = await self._http()
            response = await client.get(self._collection_url(), headers=self._headers())
            if response.status_code == 404:
                created = await client.put(
                    self._collection_url(),
                    headers=self._headers(),
                    json={"vectors": {"size": 1, "distance": "Dot"}},
                )
                if created.status_code not in {200, 201, 409}:
                    created.raise_for_status()
                response = await client.get(
                    self._collection_url(), headers=self._headers()
                )
            response.raise_for_status()
            details = response.json().get("result", {})
            vector_config = (
                details.get("config", {}).get("params", {}).get("vectors", {})
            )
            if isinstance(vector_config, dict):
                size = vector_config.get("size")
                if size is not None and int(size) != 1:
                    raise RuntimeError("Room archive collection has incompatible vector size")

            schema = details.get("payload_schema", {})
            required = {
                "room_id": "keyword",
                "event_type": "keyword",
                "order_key": "integer",
                "occurred_at_ms": "integer",
                "actor_uid": "keyword",
                "actor_name": "text",
                "message_id": "keyword",
                "content": "text",
            }
            for field, field_type in required.items():
                existing = schema.get(field)
                if existing:
                    existing_type = (
                        existing.get("data_type") if isinstance(existing, dict) else None
                    )
                    if existing_type and existing_type != field_type:
                        raise RuntimeError(
                            f"Room archive payload index {field} has incompatible type"
                        )
                    continue
                created_index = await client.put(
                    f"{self._collection_url()}/index?wait=true",
                    headers=self._headers(),
                    json={"field_name": field, "field_schema": field_type},
                )
                if created_index.status_code not in {200, 201, 409}:
                    created_index.raise_for_status()
            self.ready = True

    @staticmethod
    def _event_identity(
        room_id: str,
        event_type: str,
        source_event_id: str | None,
        message_id: str | None,
        event_time: Any,
        actor_uid: str | None,
        content: str | None,
        metadata: dict[str, Any],
    ) -> str:
        # A message creation is one room object, whether it was sent by Stella
        # or received through Anarchy's webhook echo. Edits remain separate events.
        if message_id and event_type in {"chat.message.in", "chat.message.out"}:
            return f"message:{room_id}:{message_id}"
        if source_event_id:
            return f"event:{room_id}:{source_event_id}"
        stable = {
            "room_id": room_id,
            "event_type": event_type,
            "message_id": message_id,
            "event_time": str(event_time or ""),
            "actor_uid": actor_uid,
            "content_hash": hashlib.sha256(
                str(content or "").encode("utf-8", "replace")
            ).hexdigest(),
            "metadata": metadata,
        }
        encoded = json.dumps(
            stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )
        return "fallback:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    async def record_event(
        self,
        room_id: str,
        event_type: str,
        *,
        event_time: Any = None,
        source_event_id: str | None = None,
        actor_uid: str | None = None,
        actor_name: str | None = None,
        message_id: str | None = None,
        reply_to_message_id: str | None = None,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Append one room event. Replayed webhook deliveries upsert to the same ID."""
        room = str(room_id or "").strip()
        kind = str(event_type or "").strip().casefold()
        if not room or not kind or not self.enabled:
            return False

        safe_metadata = _safe_snapshot(metadata or {})
        if not isinstance(safe_metadata, dict):
            safe_metadata = {"snapshot": safe_metadata}
        safe_content = _safe_text(content) if content is not None else None
        identity = self._event_identity(
            room,
            kind,
            str(source_event_id or "").strip() or None,
            str(message_id or "").strip() or None,
            event_time,
            str(actor_uid or "").strip() or None,
            safe_content,
            safe_metadata,
        )
        point_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"stella-room-archive:{identity}")
        )

        received_ns = time.time_ns()
        received_ms = received_ns // 1_000_000
        occurred_ms = _to_epoch_ms(event_time, received_ms)
        occurred_us = _to_epoch_us(event_time, received_ns // 1000)
        # Stable, near-microsecond ordering for webhook retries and bot echoes.
        # Qdrant integer payloads are signed 64-bit; microseconds * 1000 stays safe.
        tie_breaker = int(hashlib.sha256(identity.encode("utf-8")).hexdigest()[:6], 16) % 1000
        order_key = occurred_us * 1000 + tie_breaker
        self._last_order_key = max(self._last_order_key, order_key)
        occurred_iso = datetime.fromtimestamp(
            occurred_ms / 1000, tz=timezone.utc
        ).isoformat()
        received_iso = datetime.fromtimestamp(
            received_ms / 1000, tz=timezone.utc
        ).isoformat()

        payload: dict[str, Any] = {
            "schema_version": 1,
            "room_id": room,
            "event_type": kind,
            "occurred_at": occurred_iso,
            "occurred_at_ms": occurred_ms,
            "recorded_at": received_iso,
            "recorded_at_ms": received_ms,
            "order_key": order_key,
            "source_event_id": str(source_event_id or "")[:160],
            "actor_uid": str(actor_uid or "")[:160],
            "actor_name": _safe_text(str(actor_name or ""))[:120],
            "message_id": str(message_id or "")[:160],
            "reply_to_message_id": str(reply_to_message_id or "")[:160],
            "metadata": safe_metadata,
        }
        if safe_content is not None:
            payload["content"] = safe_content[:20000]

        try:
            await self.ensure_collection()
            client = await self._http()
            body = {
                "points": [
                    {
                        "id": point_id,
                        "vector": [1.0],
                        "payload": payload,
                    }
                ]
            }
            last_error: Exception | None = None
            for attempt in range(3):
                try:
                    response = await client.put(
                        f"{self._collection_url()}/points?wait=true",
                        headers=self._headers(),
                        json=body,
                    )
                    response.raise_for_status()
                    self.events_written += 1
                    self.last_success_at = time.time()
                    return True
                except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                    last_error = exc
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    if status in {400, 401, 403, 404, 422}:
                        break
                    if attempt < 2:
                        await asyncio.sleep(0.2 * (2 ** attempt))
            if last_error is not None:
                raise last_error
            return False
        except Exception as exc:
            self._report_failure("write", exc)
            return False

    async def list_room_events(
        self,
        room_id: str,
        *,
        after_order_key: int = 0,
        before_order_key: int | None = None,
        limit: int = 100,
        reverse: bool = False,
        actor_uid: str | None = None,
        actor_name: str | None = None,
        event_type: str | None = None,
        query: str | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """Search and page a room's archive in chronological or reverse order.

        reverse=True returns newest-first. The order_key cursor is stable across
        restarts and allows the caller to page without relying on Qdrant offsets.
        """
        room = str(room_id or "").strip()
        if not room or not self.enabled:
            return []
        must: list[dict[str, Any]] = [
            {"key": "room_id", "match": {"value": room}}
        ]
        if actor_uid:
            must.append({"key": "actor_uid", "match": {"value": str(actor_uid).strip()}})
        if actor_name:
            must.append({"key": "actor_name", "match": {"text": str(actor_name).strip()}})
        if event_type:
            must.append({"key": "event_type", "match": {"value": str(event_type).strip()}})
        if query:
            must.append({"key": "content", "match": {"text": str(query).strip()}})
        time_range: dict[str, int] = {}
        if start_ms is not None:
            time_range["gte"] = int(start_ms)
        if end_ms is not None:
            time_range["lte"] = int(end_ms)
        if time_range:
            must.append({"key": "occurred_at_ms", "range": time_range})

        order_by: dict[str, Any] = {
            "key": "order_key",
            "direction": "desc" if reverse else "asc",
        }
        if reverse and before_order_key is not None:
            order_by["start_from"] = max(0, int(before_order_key) - 1)
        elif not reverse and after_order_key:
            order_by["start_from"] = max(0, int(after_order_key) + 1)

        try:
            await self.ensure_collection()
            client = await self._http()
            response = await client.post(
                f"{self._collection_url()}/points/scroll",
                headers=self._headers(),
                json={
                    "filter": {"must": must},
                    "limit": max(1, min(500, int(limit))),
                    "with_payload": True,
                    "with_vector": False,
                    "order_by": order_by,
                },
            )
            response.raise_for_status()
            result = response.json().get("result", {})
            points = result.get("points", [])
            return [
                dict(item.get("payload") or {})
                for item in points
                if isinstance(item, dict)
            ]
        except Exception as exc:
            self._report_failure("read", exc)
            return []

    async def search_room_events(
        self,
        room_id: str,
        query: str,
        *,
        actor_uid: str | None = None,
        actor_name: str | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Full-text search within one room, newest matches first."""
        return await self.list_room_events(
            room_id,
            reverse=True,
            query=query,
            actor_uid=actor_uid,
            actor_name=actor_name,
            start_ms=start_ms,
            end_ms=end_ms,
            limit=limit,
        )

    async def recent_room_context(
        self, room_id: str, *, limit: int = 24
    ) -> list[dict[str, Any]]:
        """Return recent message events newest-first for conversational context."""
        events = await self.list_room_events(
            room_id,
            reverse=True,
            limit=max(1, min(80, int(limit))),
        )
        return [
            event for event in events
            if str(event.get("event_type") or "").casefold() in {
                "chat.message.in", "chat.message.out", "chat.message.edited",
                "message.created", "message.create", "message.edited",
            }
            and str(event.get("content") or "").strip()
        ]

    async def integrity_check(
        self, room_id: str, *, sample_limit: int = 500
    ) -> dict[str, Any]:
        """Inspect recent archive records for missing IDs, timestamps, and duplicates."""
        total = await self.count_room_events(room_id)
        events = await self.list_room_events(
            room_id, reverse=True, limit=max(1, min(500, int(sample_limit)))
        )
        message_ids: set[str] = set()
        event_ids: set[str] = set()
        duplicate_messages = 0
        duplicate_events = 0
        missing_message_ids = 0
        missing_timestamps = 0
        for item in events:
            kind = str(item.get("event_type") or "")
            message_id = str(item.get("message_id") or "").strip()
            source_id = str(item.get("source_event_id") or "").strip()
            if kind in {"chat.message.in", "chat.message.out"}:
                if not message_id:
                    missing_message_ids += 1
                elif message_id in message_ids:
                    duplicate_messages += 1
                else:
                    message_ids.add(message_id)
            if source_id:
                if source_id in event_ids:
                    duplicate_events += 1
                else:
                    event_ids.add(source_id)
            if not item.get("occurred_at_ms"):
                missing_timestamps += 1
        return {
            "total": total,
            "sampled": len(events),
            "sample_limit": max(1, min(500, int(sample_limit))),
            "sample_truncated": total > len(events),
            "missing_message_ids": missing_message_ids,
            "missing_timestamps": missing_timestamps,
            "duplicate_message_ids": duplicate_messages,
            "duplicate_event_ids": duplicate_events,
        }

    async def count_room_events(self, room_id: str) -> int:
        room = str(room_id or "").strip()
        if not room or not self.enabled:
            return 0
        try:
            await self.ensure_collection()
            client = await self._http()
            response = await client.post(
                f"{self._collection_url()}/points/count",
                headers=self._headers(),
                json={
                    "exact": True,
                    "filter": {
                        "must": [{"key": "room_id", "match": {"value": room}}]
                    },
                },
            )
            response.raise_for_status()
            return int(response.json().get("result", {}).get("count", 0))
        except Exception as exc:
            self._report_failure("count", exc)
            return 0
