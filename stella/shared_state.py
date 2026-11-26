"""Cross-instance shared settings backed by the existing Qdrant service."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from urllib.parse import quote

import httpx


class QdrantSharedState:
    """Small key/value store that remains consistent across Render instances."""

    def __init__(
        self,
        *,
        url: str | None = None,
        api_key: str | None = None,
        collection: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.url = (url if url is not None else os.getenv("QDRANT_URL", "")).strip().rstrip("/")
        self.api_key = (api_key if api_key is not None else os.getenv("QDRANT_API_KEY", "")).strip()
        self.collection = (
            collection
            if collection is not None
            else os.getenv("QDRANT_GLOBAL_STATE_COLLECTION", "stella_global_state")
        ).strip()
        self.client = client
        self._owns_client = client is None
        self._collection_lock = asyncio.Lock()
        self._collection_ready = False

    @property
    def configured(self) -> bool:
        return bool(self.url and self.api_key and self.collection)

    def _headers(self) -> dict[str, str]:
        return {"api-key": self.api_key, "Content-Type": "application/json"}

    async def _http(self) -> httpx.AsyncClient:
        if not self.configured:
            raise RuntimeError("Qdrant is not configured for shared global settings")
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(12, connect=4),
                follow_redirects=False,
            )
            self._owns_client = True
        return self.client

    def _collection_url(self) -> str:
        return f"{self.url}/collections/{quote(self.collection, safe='')}"

    @staticmethod
    def _point_id(key: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"stella-global-setting:{key}"))

    async def _ensure_collection(self) -> None:
        if self._collection_ready:
            return
        async with self._collection_lock:
            if self._collection_ready:
                return
            client = await self._http()
            response = await client.get(
                self._collection_url(),
                headers=self._headers(),
            )
            if response.status_code == 404:
                create = await client.put(
                    self._collection_url(),
                    headers=self._headers(),
                    json={"vectors": {"size": 1, "distance": "Dot"}},
                )
                # Another Render instance may create it at the same time.
                if create.status_code not in {200, 201, 409}:
                    create.raise_for_status()
                response = await client.get(
                    self._collection_url(),
                    headers=self._headers(),
                )
            response.raise_for_status()
            vectors = (
                response.json()
                .get("result", {})
                .get("config", {})
                .get("params", {})
                .get("vectors", {})
            )
            size = vectors.get("size") if isinstance(vectors, dict) else None
            if size is not None and int(size) != 1:
                raise RuntimeError("Qdrant shared-state collection has an incompatible vector size")
            self._collection_ready = True

    async def get_setting(self, key: str) -> str | None:
        """Return a shared value, or None when the key has never been written."""
        await self._ensure_collection()
        client = await self._http()
        response = await client.get(
            f"{self._collection_url()}/points/{self._point_id(key)}",
            headers=self._headers(),
            params={"with_payload": "true", "with_vector": "false"},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        result = response.json().get("result")
        payload = result.get("payload", {}) if isinstance(result, dict) else {}
        if payload.get("key") != key or payload.get("value") is None:
            return None
        return str(payload["value"])

    async def set_setting(self, key: str, value: str) -> None:
        """Upsert a stable point ID so all running instances see the same value."""
        await self._ensure_collection()
        client = await self._http()
        response = await client.put(
            f"{self._collection_url()}/points",
            headers=self._headers(),
            params={"wait": "true"},
            json={
                "points": [{
                    "id": self._point_id(key),
                    "vector": [0.0],
                    "payload": {
                        "key": key,
                        "value": str(value),
                        "updated_at": int(time.time()),
                    },
                }]
            },
        )
        response.raise_for_status()

    async def close(self) -> None:
        if self._owns_client and self.client is not None and not self.client.is_closed:
            await self.client.aclose()
