"""Opt-in, durable AI conversation memory backed by Qdrant."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from typing import Any
from urllib.parse import quote

import httpx

log = logging.getLogger("stella.ai_memory")


class QdrantConversationMemory:
    """Stores a small rolling transcript per room/member, with explicit opt-in at the caller."""

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self.url = os.getenv("QDRANT_URL", "").strip().rstrip("/")
        self.api_key = os.getenv("QDRANT_API_KEY", "").strip()
        self.collection = os.getenv("QDRANT_AI_MEMORY_COLLECTION", "stella_ai_memory").strip()
        self.retention_days = max(1, min(90, int(os.getenv("AI_MEMORY_RETENTION_DAYS", "30"))))
        self.max_turns = 10
        self.client = client
        self._owns_client = client is None
        self._ready = False
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.api_key and self.collection)

    @property
    def status(self) -> str:
        if not self.enabled:
            return "no configurada: faltan QDRANT_URL o QDRANT_API_KEY"
        return f"Qdrant disponible; retención de {self.retention_days} días por usuario y sala"

    def _headers(self) -> dict[str, str]:
        return {"api-key": self.api_key, "Content-Type": "application/json"}

    def _collection_url(self) -> str:
        return f"{self.url}/collections/{quote(self.collection, safe='')}"

    async def _http(self) -> httpx.AsyncClient:
        if not self.enabled:
            raise RuntimeError("Qdrant AI memory is not configured")
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(12, connect=4),
                follow_redirects=False,
            )
            self._owns_client = True
        return self.client

    @staticmethod
    def _identity(room_id: str, uid: str) -> tuple[str, str]:
        raw = f"{str(room_id)}\0{str(uid)}"
        digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"stella-ai-memory:{digest}"))
        return digest, point_id

    async def ensure_collection(self) -> None:
        if self._ready:
            return
        async with self._lock:
            if self._ready:
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
                response = await client.get(self._collection_url(), headers=self._headers())
            response.raise_for_status()
            vectors = (
                response.json().get("result", {})
                .get("config", {}).get("params", {}).get("vectors", {})
            )
            size = vectors.get("size") if isinstance(vectors, dict) else None
            if size is not None and int(size) != 1:
                raise RuntimeError("Qdrant AI memory collection has an incompatible vector size")
            self._ready = True

    @staticmethod
    def _clean_turns(turns: list[dict[str, Any]], limit: int = 10) -> list[dict[str, str]]:
        cleaned: list[dict[str, str]] = []
        safe_limit = max(1, min(20, int(limit)))
        source = turns if isinstance(turns, list) else []
        for item in source[-safe_limit:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "")
            if role not in {"user", "assistant"}:
                continue
            content = " ".join(str(item.get("content") or "").split())
            if not content:
                continue
            # Never preserve obvious credentials or secrets in long-term context.
            content = re.sub(
                r"(?i)\b(api[_ -]?key|access[_ -]?token|password|contraseña|clave|secret)\s*[:=]\s*([^\s,;]+)",
                r"\1=[REDACTED]",
                content,
            )
            content = re.sub(r"(?i)\bBearer\s+[^\s,;]+, "Bearer [REDACTED]", content)
            maximum = 1400 if role == "user" else 1200
            cleaned.append({"role": role, "content": content[:maximum]})
        return cleaned[-safe_limit:]

    async def load(self, room_id: str, uid: str) -> list[dict[str, str]]:
        if not self.enabled or not room_id or not uid:
            return []
        try:
            await self.ensure_collection()
            digest, point_id = self._identity(room_id, uid)
            client = await self._http()
            response = await client.get(
                f"{self._collection_url()}/points/{point_id}",
                headers=self._headers(),
                params={"with_payload": "true", "with_vector": "false"},
            )
            if response.status_code == 404:
                return []
            response.raise_for_status()
            result = response.json().get("result")
            payload = result.get("payload", {}) if isinstance(result, dict) else {}
            if payload.get("identity") != digest or payload.get("enabled") is not True:
                return []
            if int(payload.get("expires_at", 0)) <= int(time.time()):
                await self.forget(room_id, uid)
                return []
            raw = payload.get("turns")
            turns = json.loads(raw) if isinstance(raw, str) else raw
            return self._clean_turns(turns if isinstance(turns, list) else [], self.max_turns)
        except Exception as exc:
            log.warning("Persistent AI memory load failed (%s).", type(exc).__name__)
            return []

    async def save(self, room_id: str, uid: str, turns: list[dict[str, Any]]) -> bool:
        if not self.enabled or not room_id or not uid:
            return False
        cleaned = self._clean_turns(turns, self.max_turns)
        try:
            await self.ensure_collection()
            digest, point_id = self._identity(room_id, uid)
            client = await self._http()
            now = int(time.time())
            response = await client.put(
                f"{self._collection_url()}/points?wait=true",
                headers=self._headers(),
                json={
                    "points": [{
                        "id": point_id,
                        "vector": [0.0],
                        "payload": {
                            "identity": digest,
                            "enabled": True,
                            "turns": json.dumps(cleaned, ensure_ascii=False, separators=(",", ":")),
                            "updated_at": now,
                            "expires_at": now + self.retention_days * 86400,
                        },
                    }]
                },
            )
            response.raise_for_status()
            return True
        except Exception as exc:
            log.warning("Persistent AI memory save failed (%s).", type(exc).__name__)
            return False

    async def is_enabled(self, room_id: str, uid: str) -> bool:
        if not self.enabled or not room_id or not uid:
            return False
        try:
            await self.ensure_collection()
            digest, point_id = self._identity(room_id, uid)
            client = await self._http()
            response = await client.get(
                f"{self._collection_url()}/points/{point_id}",
                headers=self._headers(),
                params={"with_payload": "true", "with_vector": "false"},
            )
            if response.status_code == 404:
                return False
            response.raise_for_status()
            result = response.json().get("result")
            payload = result.get("payload", {}) if isinstance(result, dict) else {}
            if payload.get("identity") != digest or payload.get("enabled") is not True:
                return False
            return int(payload.get("expires_at", 0)) > int(time.time())
        except Exception as exc:
            log.warning("Persistent AI memory preference read failed (%s).", type(exc).__name__)
            return False

    async def set_enabled(self, room_id: str, uid: str, enabled: bool) -> bool:
        if not enabled:
            return await self.forget(room_id, uid)
        if not self.enabled or not room_id or not uid:
            return False
        # Persist consent in Qdrant itself, not ephemeral local settings.
        # Preserve existing turns if a record is already present.
        try:
            await self.ensure_collection()
            digest, point_id = self._identity(room_id, uid)
            client = await self._http()
            response = await client.get(
                f"{self._collection_url()}/points/{point_id}",
                headers=self._headers(),
                params={"with_payload": "true", "with_vector": "false"},
            )
            if response.status_code == 404:
                return await self.save(room_id, uid, [])
            response.raise_for_status()
            result = response.json().get("result")
            payload = result.get("payload", {}) if isinstance(result, dict) else {}
            if payload.get("identity") != digest:
                return False
            if int(payload.get("expires_at", 0)) <= int(time.time()):
                return await self.save(room_id, uid, [])
            payload["enabled"] = True
            payload["updated_at"] = int(time.time())
            payload["expires_at"] = int(time.time()) + self.retention_days * 86400
            written = await client.put(
                f"{self._collection_url()}/points?wait=true",
                headers=self._headers(),
                json={"points": [{
                    "id": point_id,
                    "vector": [0.0],
                    "payload": payload,
                }]},
            )
            written.raise_for_status()
            return True
        except Exception as exc:
            log.warning("Persistent AI memory opt-in failed (%s).", type(exc).__name__)
            return False

    async def clear(self, room_id: str, uid: str) -> bool:
        """Erase conversation turns while keeping the explicit opt-in enabled."""
        return await self.save(room_id, uid, [])

    async def forget(self, room_id: str, uid: str) -> bool:
        if not self.enabled or not room_id or not uid:
            return False
        try:
            await self.ensure_collection()
            _, point_id = self._identity(room_id, uid)
            client = await self._http()
            response = await client.post(
                f"{self._collection_url()}/points/delete?wait=true",
                headers=self._headers(),
                json={"points": [point_id]},
            )
            response.raise_for_status()
            return True
        except Exception as exc:
            log.warning("Persistent AI memory deletion failed (%s).", type(exc).__name__)
            return False

    async def close(self) -> None:
        if self._owns_client and self.client is not None and not self.client.is_closed:
            await self.client.aclose()
