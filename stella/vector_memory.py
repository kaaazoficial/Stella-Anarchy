"""Short-lived semantic memory for public room messages."""
from __future__ import annotations

import logging
import os
import re
import time
import uuid
from typing import Any

import httpx

log = logging.getLogger("stella.vector_memory")


class VectorMemory:
    """Qdrant-backed message memory with expiry and a fail-closed embedding circuit breaker."""

    def __init__(self) -> None:
        self.url = os.getenv("QDRANT_URL", "").strip().rstrip("/")
        self.api_key = os.getenv("QDRANT_API_KEY", "").strip()
        self.collection = os.getenv("QDRANT_COLLECTION", "stella_messages").strip()
        self.gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.model = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-2").strip()
        self.dimensions = int(os.getenv("GEMINI_EMBEDDING_DIMENSIONS", "768"))
        self.retention_days = max(1, min(90, int(os.getenv("MEMORY_RETENTION_DAYS", "30"))))
        self.cleanup_interval = max(3600, int(os.getenv("MEMORY_CLEANUP_INTERVAL_SECONDS", "21600")))
        self.client: httpx.AsyncClient | None = None
        self.ready = False
        self.last_cleanup = 0.0

        # Authentication failures are permanent for this process. Render restarts
        # the service after a key change, which resets the breaker safely.
        self.embedding_blocked_status: int | None = None
        self.embedding_retry_after = 0.0
        self.embedding_failure_count = 0
        self.last_failure_log_at = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.api_key and self.gemini_key)

    @property
    def status(self) -> str:
        if not self.enabled:
            return "sin configurar: faltan credenciales de Qdrant o Gemini"
        if self.embedding_blocked_status is not None:
            return (
                f"pausada: Gemini rechazó GEMINI_API_KEY (HTTP "
                f"{self.embedding_blocked_status}); revisa la clave y sus restricciones"
            )
        remaining = self.embedding_retry_after - time.monotonic()
        if remaining > 0:
            return f"en pausa temporal por un error de red/servicio ({int(remaining)} s)"
        if self.ready:
            return "conectada"
        return "configurada; se validará en el siguiente uso"

    def _can_embed(self) -> bool:
        return (
            self.enabled
            and self.embedding_blocked_status is None
            and time.monotonic() >= self.embedding_retry_after
        )

    def _report_failure(self, operation: str, exc: Exception) -> None:
        """Log a bounded summary, never a traceback or message contents."""
        now = time.monotonic()
        if now - self.last_failure_log_at < 60:
            return
        status = getattr(getattr(exc, "response", None), "status_code", None)
        reason = f"HTTP {status}" if status is not None else type(exc).__name__
        log.warning("Vector memory %s failed (%s).", operation, reason)
        self.last_failure_log_at = now

    def _schedule_retry(self) -> None:
        self.embedding_failure_count = min(self.embedding_failure_count + 1, 6)
        delay = min(300, 5 * (2 ** (self.embedding_failure_count - 1)))
        self.embedding_retry_after = time.monotonic() + delay

    async def _http(self) -> httpx.AsyncClient:
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(20, connect=8),
                follow_redirects=False,
            )
        return self.client

    async def close(self) -> None:
        if self.client and not self.client.is_closed:
            await self.client.aclose()

    def _headers(self) -> dict[str, str]:
        return {"api-key": self.api_key, "Content-Type": "application/json"}

    async def _embed(self, text: str, query: bool = False) -> list[float]:
        if self.embedding_blocked_status is not None:
            raise RuntimeError("Gemini embeddings are paused after an authentication failure")
        if time.monotonic() < self.embedding_retry_after:
            raise RuntimeError("Gemini embeddings are in temporary backoff")

        client = await self._http()
        prefix = "task: search result | query: " if query else "title: none | text: "
        response = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:embedContent",
            headers={"x-goog-api-key": self.gemini_key},
            json={
                "content": {"parts": [{"text": prefix + text[:6000]}]},
                "output_dimensionality": self.dimensions,
            },
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if response.status_code in {401, 403}:
                self.embedding_blocked_status = response.status_code
                self._report_failure("Gemini authentication", exc)
            else:
                self._schedule_retry()
                self._report_failure("Gemini embedding request", exc)
            raise

        values = response.json().get("embedding", {}).get("values", [])
        if len(values) != self.dimensions:
            self._schedule_retry()
            raise RuntimeError("Gemini embedding dimension mismatch")

        self.embedding_failure_count = 0
        self.embedding_retry_after = 0.0
        return values

    async def ensure_collection(self) -> None:
        if self.ready:
            return
        client = await self._http()
        response = await client.get(
            f"{self.url}/collections/{self.collection}",
            headers=self._headers(),
        )
        if response.status_code == 404:
            response = await client.put(
                f"{self.url}/collections/{self.collection}",
                headers=self._headers(),
                json={"vectors": {"size": self.dimensions, "distance": "Cosine"}},
            )
            response.raise_for_status()
            # Read the newly-created collection so its payload schema is checked
            # through the same path as an existing collection.
            response = await client.get(
                f"{self.url}/collections/{self.collection}",
                headers=self._headers(),
            )
            response.raise_for_status()
        else:
            response.raise_for_status()

        collection = response.json().get("result", {})
        size = (
            collection.get("config", {})
            .get("params", {})
            .get("vectors", {})
            .get("size")
        )
        if size is not None and int(size) != self.dimensions:
            raise RuntimeError("Qdrant collection has an incompatible vector size")

        # Qdrant Cloud strict mode rejects filters on fields without payload
        # indexes. Stella filters every query by room and expiry, and deletion
        # by room and user, so these three indexes are part of the schema.
        payload_schema = collection.get("payload_schema", {})
        required_indexes = {
            "room_id": "keyword",
            "uid": "keyword",
            "expires_at": "integer",
        }
        for field_name, field_type in required_indexes.items():
            existing = payload_schema.get(field_name)
            if existing:
                existing_type = existing.get("data_type") if isinstance(existing, dict) else None
                if existing_type and existing_type != field_type:
                    raise RuntimeError(
                        f"Qdrant payload index {field_name} has incompatible type"
                    )
                continue
            index_response = await client.put(
                f"{self.url}/collections/{self.collection}/index?wait=true",
                headers=self._headers(),
                json={"field_name": field_name, "field_schema": field_type},
            )
            index_response.raise_for_status()

        self.ready = True

    async def index_message(self, room: str, uid: str, name: str, content: str) -> None:
        if not self._can_embed():
            return
        text = " ".join(str(content or "").split())
        if len(text) < 8 or len(text) > 6000 or text.startswith(("!", "-", "/")):
            return
        if re.search(r"(?:api[_ -]?key|password|contraseña|token)\s*[:=]", text, re.I):
            return
        try:
            vector = await self._embed(text)
            await self.ensure_collection()
            now = int(time.time())
            client = await self._http()
            response = await client.put(
                f"{self.url}/collections/{self.collection}/points?wait=true",
                headers=self._headers(),
                json={
                    "points": [{
                        "id": str(uuid.uuid4()),
                        "vector": vector,
                        "payload": {
                            "room_id": room,
                            "uid": uid,
                            "name": name[:80],
                            "content": text,
                            "created_at": now,
                            "expires_at": now + self.retention_days * 86400,
                        },
                    }]
                },
            )
            response.raise_for_status()
        except Exception as exc:
            if self.embedding_blocked_status is None:
                self._schedule_retry()
                self._report_failure("message indexing", exc)

    async def search(self, room: str, query: str, uid: str, limit: int = 5) -> list[dict[str, Any]]:
        if not self._can_embed() or not query.strip():
            return []
        try:
            vector = await self._embed(query, query=True)
            await self.ensure_collection()
            client = await self._http()
            response = await client.post(
                f"{self.url}/collections/{self.collection}/points/search",
                headers=self._headers(),
                json={
                    "vector": vector,
                    "limit": max(1, min(8, limit)),
                    "with_payload": True,
                    "filter": {
                        "must": [
                            {"key": "room_id", "match": {"value": room}},
                            {"key": "uid", "match": {"value": uid}},
                            {"key": "expires_at", "range": {"gt": int(time.time())}},
                        ]
                    },
                },
            )
            response.raise_for_status()
            return [
                point.get("payload", {})
                for point in response.json().get("result", [])
                if point.get("payload", {}).get("content")
            ]
        except Exception as exc:
            if self.embedding_blocked_status is None:
                self._schedule_retry()
                self._report_failure("search", exc)
            return []

    async def forget_user(self, room: str, uid: str) -> None:
        if not self.enabled:
            return
        try:
            await self.ensure_collection()
            client = await self._http()
            response = await client.post(
                f"{self.url}/collections/{self.collection}/points/delete?wait=true",
                headers=self._headers(),
                json={
                    "filter": {
                        "must": [
                            {"key": "room_id", "match": {"value": room}},
                            {"key": "uid", "match": {"value": uid}},
                        ]
                    }
                },
            )
            response.raise_for_status()
        except Exception as exc:
            self._report_failure("user deletion", exc)

    async def cleanup(self) -> None:
        if not self.enabled or time.time() - self.last_cleanup < self.cleanup_interval:
            return
        try:
            await self.ensure_collection()
            client = await self._http()
            response = await client.post(
                f"{self.url}/collections/{self.collection}/points/delete?wait=true",
                headers=self._headers(),
                json={
                    "filter": {
                        "must": [
                            {"key": "expires_at", "range": {"lt": int(time.time())}}
                        ]
                    }
                },
            )
            response.raise_for_status()
            self.last_cleanup = time.time()
        except Exception as exc:
            self._report_failure("retention cleanup", exc)
