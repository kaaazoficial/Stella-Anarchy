"""Append-only, chronological archive of every Anarchy room event in Qdrant."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
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

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.api_key and self.collection)

    @property
    def status(self) -> str:
        if not self.enabled:
            return "no configurado: faltan QDRANT_URL o QDRANT_API_KEY"
        if self.ready:
            return "conectado: archivo cronológico activo, sin caducidad automática"
        return "configurado: el archivo se inicializará al recibir el primer evento"

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
        order_key = max(received_ns, self._last_order_key + 1)
        self._last_order_key = order_key
        received_ms = received_ns // 1_000_000
        occurred_ms = _to_epoch_ms(event_time, received_ms)
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
            response = await client.put(
                f"{self._collection_url()}/points?wait=true",
                headers=self._headers(),
                json={
                    "points": [
                        {
                            "id": point_id,
                            "vector": [1.0],
                            "payload": payload,
                        }
                    ]
                },
            )
            response.raise_for_status()
            self.events_written += 1
            return True
        except Exception as exc:
            self._report_failure("write", exc)
            return False

    async def list_room_events(
        self,
        room_id: str,
        *,
        after_order_key: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read a room's archive in insertion order using Qdrant's indexed payload sort."""
        room = str(room_id or "").strip()
        if not room or not self.enabled:
            return []
        try:
            await self.ensure_collection()
            client = await self._http()
            response = await client.post(
                f"{self._collection_url()}/points/scroll",
                headers=self._headers(),
                json={
                    "filter": {
                        "must": [{"key": "room_id", "match": {"value": room}}]
                    },
                    "limit": max(1, min(500, int(limit))),
                    "with_payload": True,
                    "with_vector": False,
                    "order_by": {
                        "key": "order_key",
                        "direction": "asc",
                        "start_from": max(0, int(after_order_key) + 1),
                    },
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
