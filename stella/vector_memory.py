"""Short-lived semantic memory for community messages, isolated by room and member."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import random
import re
import time
import uuid
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import httpx

from stella.config import get_gemini_embedding_api_key

log = logging.getLogger("stella.vector_memory")

# The semantic message store is opt-in per member. Avoid indexing obvious secrets,
# contact details, or sensitive personal disclosures even after opt-in.
_SECRET_RE = re.compile(
    r"(?i)(?:api[_ -]?key|access[_ -]?token|token|password|contraseñ|clave|secret|"
    r"bearer|private[_ -]?key)\s*[:=]|\bBearer\s+\S+|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
_CONTACT_RE = re.compile(
    r"(?i)(?:\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|"
    r"(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w))"
)
_SENSITIVE_RE = re.compile(
    r"(?i)\b(?:mi diagn[oó]stico|mi enfermedad|mi trastorno|mi tratamiento|"
    r"me diagnosticaron|mi salud mental|mi orientaci[oó]n sexual|mi vida sexual|"
    r"mi religi[oó]n|mi partido pol[ií]tico|mi domicilio|mi direcci[oó]n|"
    r"mi n[uú]mero de tel[eé]fono|mi correo electr[oó]nico)\b"
)


class VectorMemory:
    """Qdrant-backed semantic message memory with separate provider circuit breakers."""

    def __init__(self) -> None:
        self.url = os.getenv("QDRANT_URL", "").strip().rstrip("/")
        self.api_key = os.getenv("QDRANT_API_KEY", "").strip()
        self.collection = os.getenv("QDRANT_COLLECTION", "stella_messages").strip()
        self.gemini_key = get_gemini_embedding_api_key()
        self.gemini_key_source = (
            "GEMINI_EMBEDDING_API_KEY"
            if os.getenv("GEMINI_EMBEDDING_API_KEY", "").strip()
            else "GOOGLE_API_KEY"
            if os.getenv("GOOGLE_API_KEY", "").strip()
            else "GEMINI_API_KEY"
            if os.getenv("GEMINI_API_KEY", "").strip()
            else "none"
        )
        self.model = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-2").strip()
        self.dimensions = max(128, min(3072, int(os.getenv("GEMINI_EMBEDDING_DIMENSIONS", "768"))))
        self.retention_days = max(1, min(90, int(os.getenv("MEMORY_RETENTION_DAYS", "30"))))
        self.cleanup_interval = max(3600, int(os.getenv("MEMORY_CLEANUP_INTERVAL_SECONDS", "21600")))
        self.embedding_cache_ttl = max(0, min(600, int(os.getenv("GEMINI_EMBEDDING_CACHE_TTL_SECONDS", "180"))))
        self.embedding_cache_limit = max(16, min(1024, int(os.getenv("GEMINI_EMBEDDING_CACHE_SIZE", "256"))))
        try:
            self.score_threshold = max(0.0, min(1.0, float(os.getenv("QDRANT_SCORE_THRESHOLD", "0.35"))))
        except (TypeError, ValueError):
            self.score_threshold = 0.35
        self.client: httpx.AsyncClient | None = None
        self.ready = False
        self.last_cleanup = 0.0
        self._collection_lock = asyncio.Lock()

        # Gemini and Qdrant have independent breakers: a Qdrant outage must not
        # masquerade as a Gemini embedding failure, and vice versa.
        self.embedding_blocked_status: int | None = None
        self.embedding_retry_after = 0.0
        self.embedding_failure_count = 0
        self.qdrant_blocked_status: int | None = None
        self.qdrant_retry_after = 0.0
        self.qdrant_failure_count = 0
        self.last_failure_log_at = 0.0

        # Cache only vectors, indexed by a one-way digest. No message/query text
        # is kept in this cache. In-flight tasks coalesce simultaneous duplicates.
        self._embedding_cache: dict[str, tuple[float, tuple[float, ...]]] = {}
        self._embedding_inflight: dict[str, asyncio.Task[list[float]]] = {}
        self._embedding_cache_lock = asyncio.Lock()
        self.metrics: dict[str, int] = {
            "embedding_requests": 0,
            "embedding_cache_hits": 0,
            "embedding_coalesced": 0,
            "embedding_failures": 0,
            "qdrant_requests": 0,
            "qdrant_failures": 0,
            "indexed_messages": 0,
            "duplicate_message_upserts": 0,
            "searches": 0,
            "search_results": 0,
        }

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.api_key and self.gemini_key and self.collection)

    @property
    def status(self) -> str:
        if not self.enabled:
            missing = []
            if not self.url or not self.api_key or not self.collection:
                missing.append("Qdrant")
            if not self.gemini_key:
                missing.append("Gemini Embeddings")
            return "sin configurar: faltan credenciales de " + " y ".join(missing)
        if not self.url or not self.api_key or not self.collection:
            qdrant = "Qdrant sin configurar"
        elif self.qdrant_blocked_status is not None:
            qdrant = f"Qdrant pausado (HTTP {self.qdrant_blocked_status})"
        elif self.qdrant_retry_after > time.monotonic():
            qdrant = f"Qdrant en espera ({max(1, int(self.qdrant_retry_after - time.monotonic()))} s)"
        elif self.ready:
            qdrant = "Qdrant conectado"
        else:
            qdrant = "Qdrant configurado"

        if not self.gemini_key:
            gemini = "Gemini Embeddings sin configurar"
        elif self.embedding_blocked_status is not None:
            gemini = f"Gemini Embeddings pausado (HTTP {self.embedding_blocked_status})"
        elif self.embedding_retry_after > time.monotonic():
            gemini = f"Gemini Embeddings en espera ({max(1, int(self.embedding_retry_after - time.monotonic()))} s)"
        else:
            gemini = f"Gemini Embeddings listo ({self.model}, {self.dimensions} dimensiones)"
        return f"{qdrant}; {gemini}; retención {self.retention_days} días"

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "ready": self.ready,
            "collection": self.collection,
            "embedding_model": self.model,
            "embedding_dimensions": self.dimensions,
            "embedding_key_source": self.gemini_key_source,
            "retention_days": self.retention_days,
            "gemini_blocked_status": self.embedding_blocked_status,
            "gemini_retry_seconds": max(0, int(self.embedding_retry_after - time.monotonic())),
            "qdrant_blocked_status": self.qdrant_blocked_status,
            "qdrant_retry_seconds": max(0, int(self.qdrant_retry_after - time.monotonic())),
            "cache_entries": len(self._embedding_cache),
            "metrics": dict(self.metrics),
        }

    def _can_embed(self) -> bool:
        return bool(
            self.gemini_key
            and self.embedding_blocked_status is None
            and time.monotonic() >= self.embedding_retry_after
        )

    def _can_query_qdrant(self) -> bool:
        return bool(
            self.url
            and self.api_key
            and self.collection
            and self.qdrant_blocked_status is None
            and time.monotonic() >= self.qdrant_retry_after
        )

    def _report_failure(self, operation: str, exc: Exception) -> None:
        """Log bounded diagnostics only; never log prompt, payload, or credentials."""
        now = time.monotonic()
        if now - self.last_failure_log_at < 60:
            return
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        reason = f"HTTP {status}" if status is not None else type(exc).__name__
        detail = ""
        if response is not None and status not in {400, 401, 403}:
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    error = payload.get("error") or payload.get("status", {}).get("error")
                    if isinstance(error, dict):
                        detail = str(error.get("status") or error.get("code") or "")[:80]
            except Exception:
                detail = ""
        log.warning("Vector memory %s failed (%s)%s.", operation, reason, f" · {detail}" if detail else "")
        self.last_failure_log_at = now

    @staticmethod
    def _retry_after_seconds(response: Any, fallback: float) -> float:
        headers = getattr(response, "headers", {}) or {}
        raw = str(headers.get("retry-after") or headers.get("Retry-After") or "").strip()
        if not raw:
            return fallback
        try:
            delay = float(raw)
        except (TypeError, ValueError):
            try:
                parsed = parsedate_to_datetime(raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                delay = parsed.timestamp() - time.time()
            except (TypeError, ValueError, OverflowError):
                return fallback
        return max(1.0, min(300.0, delay))

    def _schedule_embedding_retry(self, response: Any = None) -> None:
        self.embedding_failure_count = min(self.embedding_failure_count + 1, 8)
        fallback = min(300.0, 5.0 * (2 ** (self.embedding_failure_count - 1)))
        delay = self._retry_after_seconds(response, fallback)
        self.embedding_retry_after = time.monotonic() + delay * random.uniform(0.9, 1.1)

    def _schedule_qdrant_retry(self, response: Any = None) -> None:
        self.qdrant_failure_count = min(self.qdrant_failure_count + 1, 8)
        fallback = min(300.0, 5.0 * (2 ** (self.qdrant_failure_count - 1)))
        delay = self._retry_after_seconds(response, fallback)
        self.qdrant_retry_after = time.monotonic() + delay * random.uniform(0.9, 1.1)

    def _record_embedding_failure(self, exc: Exception) -> None:
        self.metrics["embedding_failures"] += 1
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        # These statuses normally require configuration/model/key changes; retrying
        # every chat message only spends quota and adds latency.
        if status in {400, 401, 403, 404}:
            self.embedding_blocked_status = int(status)
        else:
            self._schedule_embedding_retry(response)
        self._report_failure("Gemini embedding request", exc)

    def _record_qdrant_failure(self, exc: Exception) -> None:
        self.metrics["qdrant_failures"] += 1
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if status in {400, 401, 403}:
            self.qdrant_blocked_status = int(status)
        else:
            if status == 404:
                # A collection may have been recreated outside this process.
                self.ready = False
            self._schedule_qdrant_retry(response)
        self._report_failure("Qdrant request", exc)

    async def _http(self) -> httpx.AsyncClient:
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(18.0, connect=3.0, pool=3.0),
                follow_redirects=False,
                limits=httpx.Limits(
                    max_connections=24,
                    max_keepalive_connections=12,
                    keepalive_expiry=30.0,
                ),
                headers={"User-Agent": "StellaCommunityBot/1.0"},
                trust_env=False,
            )
        return self.client

    async def close(self) -> None:
        if self.client and not self.client.is_closed:
            await self.client.aclose()

    def _headers(self) -> dict[str, str]:
        return {"api-key": self.api_key, "Content-Type": "application/json"}

    def _embedding_cache_key(self, text: str, query: bool) -> str:
        profile = f"{self.model}\0{self.dimensions}\0{int(query)}\0{text}"
        return hashlib.sha256(profile.encode("utf-8", "replace")).hexdigest()

    async def _embed_uncached(self, text: str, query: bool) -> list[float]:
        if not self._can_embed():
            raise RuntimeError("Gemini Embeddings are not currently available")
        client = await self._http()
        # Gemini Embedding 2 does not accept task_type. Google recommends these
        # asymmetric prefixes for search retrieval; keep them identical for all
        # documents and queries stored in this collection.
        prompt = (
            f"task: search result | query: {text[:6000]}"
            if query else f"title: none | text: {text[:6000]}"
        )
        self.metrics["embedding_requests"] += 1
        response = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{quote(self.model, safe='')}:embedContent",
            headers={"x-goog-api-key": self.gemini_key},
            json={
                "content": {"parts": [{"text": prompt}]},
                "output_dimensionality": self.dimensions,
            },
        )
        try:
            response.raise_for_status()
            payload = response.json()
            values = payload.get("embedding", {}).get("values", [])
            if (
                not isinstance(values, list)
                or len(values) != self.dimensions
                or any(
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not math.isfinite(float(value))
                    for value in values
                )
            ):
                raise RuntimeError("Gemini returned an invalid embedding vector")
            vector = [float(value) for value in values]
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_embedding_failure(exc)
            raise

        self.embedding_failure_count = 0
        self.embedding_retry_after = 0.0
        return vector

    async def _embed(self, text: str, query: bool = False) -> list[float]:
        clean = " ".join(str(text or "").split())
        if not clean:
            raise ValueError("Embedding text cannot be empty")
        if not self._can_embed():
            raise RuntimeError("Gemini Embeddings are not currently available")
        key = self._embedding_cache_key(clean[:6000], query)
        now = time.monotonic()
        async with self._embedding_cache_lock:
            cached = self._embedding_cache.get(key)
            if cached and cached[0] > now:
                self.metrics["embedding_cache_hits"] += 1
                return list(cached[1])
            if cached:
                self._embedding_cache.pop(key, None)
            task = self._embedding_inflight.get(key)
            if task is None:
                task = asyncio.create_task(
                    self._embed_uncached(clean, query),
                    name="stella-gemini-embedding",
                )
                self._embedding_inflight[key] = task
            else:
                self.metrics["embedding_coalesced"] += 1

        try:
            vector = await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception:
            async with self._embedding_cache_lock:
                if self._embedding_inflight.get(key) is task:
                    self._embedding_inflight.pop(key, None)
            raise

        async with self._embedding_cache_lock:
            if self._embedding_inflight.get(key) is task:
                self._embedding_inflight.pop(key, None)
                if self.embedding_cache_ttl:
                    self._embedding_cache[key] = (
                        time.monotonic() + self.embedding_cache_ttl,
                        tuple(vector),
                    )
                    while len(self._embedding_cache) > self.embedding_cache_limit:
                        self._embedding_cache.pop(next(iter(self._embedding_cache)))
        return list(vector)

    async def ensure_collection(self) -> None:
        if self.ready:
            return
        if not self._can_query_qdrant():
            raise RuntimeError("Qdrant is not currently available")
        async with self._collection_lock:
            if self.ready:
                return
            try:
                await self._ensure_collection_locked()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._record_qdrant_failure(exc)
                raise

    async def _ensure_collection_locked(self) -> None:
        client = await self._http()
        collection_url = f"{self.url}/collections/{quote(self.collection, safe='')}"
        response = await client.get(collection_url, headers=self._headers())
        if response.status_code == 404:
            created = await client.put(
                collection_url,
                headers=self._headers(),
                json={"vectors": {"size": self.dimensions, "distance": "Cosine"}},
            )
            # Another Render worker may create the collection at the same time.
            if created.status_code not in {200, 201, 409}:
                created.raise_for_status()
            response = await client.get(collection_url, headers=self._headers())
        response.raise_for_status()

        collection = response.json().get("result", {})
        vectors = (
            collection.get("config", {})
            .get("params", {})
            .get("vectors", {})
        )
        size = vectors.get("size") if isinstance(vectors, dict) else None
        if size is not None and int(size) != self.dimensions:
            raise RuntimeError(
                "Qdrant collection dimension mismatch; configure the original "
                "GEMINI_EMBEDDING_DIMENSIONS or migrate the collection before changing it."
            )

        # Create payload indexes before relying on filtered retrieval. Qdrant
        # recommends indexed payload fields for efficient filtered vector search.
        payload_schema = collection.get("payload_schema", {})
        required_indexes = {
            "room_id": "keyword",
            "uid": "keyword",
            "expires_at": "integer",
        }
        for field_name, field_type in required_indexes.items():
            existing = payload_schema.get(field_name) if isinstance(payload_schema, dict) else None
            if existing:
                existing_type = existing.get("data_type") if isinstance(existing, dict) else None
                if existing_type and existing_type != field_type:
                    raise RuntimeError(
                        f"Qdrant payload index {field_name} has incompatible type"
                    )
                continue
            index_response = await client.put(
                f"{collection_url}/index?wait=true",
                headers=self._headers(),
                json={"field_name": field_name, "field_schema": field_type},
            )
            # The index might have been created concurrently by another instance.
            if index_response.status_code not in {200, 201, 409}:
                index_response.raise_for_status()

        self.ready = True
        self.qdrant_failure_count = 0
        self.qdrant_retry_after = 0.0

    @staticmethod
    def _is_private_or_secret(text: str) -> bool:
        return bool(_SECRET_RE.search(text) or _CONTACT_RE.search(text) or _SENSITIVE_RE.search(text))

    @staticmethod
    def _point_id(room: str, uid: str, content: str, day_bucket: int) -> str:
        digest = hashlib.sha256(
            f"{room}\0{uid}\0{day_bucket}\0{content.casefold()}".encode("utf-8", "replace")
        ).hexdigest()
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"stella-message:{digest}"))

    async def index_message(self, room: str, uid: str, name: str, content: str) -> None:
        if not self.enabled or not self._can_query_qdrant() or not self._can_embed():
            return
        text = " ".join(str(content or "").split())
        if len(text) < 8 or len(text) > 6000 or text.startswith(("!", "-", "/")):
            return
        if self._is_private_or_secret(text):
            return

        # Confirm the vector schema and filter indexes before spending a Gemini
        # request or creating a point. Qdrant errors do not open Gemini's breaker.
        try:
            await self.ensure_collection()
        except asyncio.CancelledError:
            raise
        except Exception:
            return

        try:
            vector = await self._embed(text)
        except asyncio.CancelledError:
            raise
        except Exception:
            return

        now = int(time.time())
        day_bucket = now // 86400
        point_id = self._point_id(str(room), str(uid), text, day_bucket)
        client = await self._http()
        try:
            self.metrics["qdrant_requests"] += 1
            response = await client.put(
                f"{self.url}/collections/{quote(self.collection, safe='')}/points?wait=true",
                headers=self._headers(),
                json={
                    "points": [{
                        "id": point_id,
                        "vector": vector,
                        "payload": {
                            "room_id": str(room),
                            "uid": str(uid),
                            "name": str(name or "Usuario")[:80],
                            "content": text,
                            "content_hash": hashlib.sha256(text.casefold().encode("utf-8", "replace")).hexdigest(),
                            "embedding_model": self.model,
                            "embedding_dimensions": self.dimensions,
                            "created_at": now,
                            "expires_at": now + self.retention_days * 86400,
                        },
                    }]
                },
            )
            response.raise_for_status()
            self.metrics["indexed_messages"] += 1
            self.qdrant_failure_count = 0
            self.qdrant_retry_after = 0.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_qdrant_failure(exc)

    async def search(self, room: str, query: str, uid: str, limit: int = 5) -> list[dict[str, Any]]:
        search_text = " ".join(str(query or "").split())
        if not search_text or not self.enabled or not self._can_query_qdrant() or not self._can_embed():
            return []
        try:
            await self.ensure_collection()
        except asyncio.CancelledError:
            raise
        except Exception:
            return []

        try:
            vector = await self._embed(search_text, query=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            return []

        try:
            self.metrics["qdrant_requests"] += 1
            self.metrics["searches"] += 1
            client = await self._http()
            response = await client.post(
                f"{self.url}/collections/{quote(self.collection, safe='')}/points/search",
                headers=self._headers(),
                json={
                    "vector": vector,
                    "limit": max(1, min(8, int(limit))),
                    "score_threshold": self.score_threshold,
                    "with_payload": True,
                    "filter": {
                        "must": [
                            {"key": "room_id", "match": {"value": str(room)}},
                            {"key": "uid", "match": {"value": str(uid)}},
                            {"key": "expires_at", "range": {"gt": int(time.time())}},
                        ]
                    },
                },
            )
            response.raise_for_status()
            points = response.json().get("result", [])
            results = [
                point.get("payload", {})
                for point in points
                if isinstance(point, dict)
                and point.get("payload", {}).get("content")
                and point.get("payload", {}).get("embedding_model", self.model) == self.model
                and int(point.get("payload", {}).get("embedding_dimensions", self.dimensions)) == self.dimensions
                and float(point.get("score", 0.0)) >= self.score_threshold
            ]
            self.metrics["search_results"] += len(results)
            self.qdrant_failure_count = 0
            self.qdrant_retry_after = 0.0
            return results
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_qdrant_failure(exc)
            return []

    async def forget_user(self, room: str, uid: str) -> None:
        if not self._can_query_qdrant():
            return
        try:
            await self.ensure_collection()
            client = await self._http()
            self.metrics["qdrant_requests"] += 1
            response = await client.post(
                f"{self.url}/collections/{quote(self.collection, safe='')}/points/delete?wait=true",
                headers=self._headers(),
                json={
                    "filter": {
                        "must": [
                            {"key": "room_id", "match": {"value": str(room)}},
                            {"key": "uid", "match": {"value": str(uid)}},
                        ]
                    }
                },
            )
            response.raise_for_status()
            self.qdrant_failure_count = 0
            self.qdrant_retry_after = 0.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_qdrant_failure(exc)

    async def cleanup(self) -> None:
        if not self._can_query_qdrant() or time.time() - self.last_cleanup < self.cleanup_interval:
            return
        try:
            await self.ensure_collection()
            client = await self._http()
            self.metrics["qdrant_requests"] += 1
            response = await client.post(
                f"{self.url}/collections/{quote(self.collection, safe='')}/points/delete?wait=true",
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
            self.qdrant_failure_count = 0
            self.qdrant_retry_after = 0.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_qdrant_failure(exc)
