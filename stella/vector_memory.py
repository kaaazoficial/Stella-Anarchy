"""Short-lived semantic memory for public room messages."""
from __future__ import annotations
import logging, os, re, time, uuid
from typing import Any
import httpx

log = logging.getLogger("stella.vector_memory")

class VectorMemory:
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

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.api_key and self.gemini_key)

    async def _http(self):
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(timeout=httpx.Timeout(20, connect=8), follow_redirects=False)
        return self.client

    async def close(self):
        if self.client and not self.client.is_closed:
            await self.client.aclose()

    def _headers(self):
        return {"api-key": self.api_key, "Content-Type": "application/json"}

    async def _embed(self, text: str, query: bool = False) -> list[float]:
        client = await self._http()
        prefix = "task: search result | query: " if query else "title: none | text: "
        response = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:embedContent",
            headers={"x-goog-api-key": self.gemini_key},
            json={"content": {"parts": [{"text": prefix + text[:6000]}]},
                  "output_dimensionality": self.dimensions},
        )
        response.raise_for_status()
        values = response.json().get("embedding", {}).get("values", [])
        if len(values) != self.dimensions:
            raise RuntimeError("Gemini embedding dimension mismatch")
        return values

    async def ensure_collection(self):
        if self.ready:
            return
        client = await self._http()
        response = await client.get(f"{self.url}/collections/{self.collection}", headers=self._headers())
        if response.status_code == 404:
            response = await client.put(
                f"{self.url}/collections/{self.collection}", headers=self._headers(),
                json={"vectors": {"size": self.dimensions, "distance": "Cosine"}},
            )
            response.raise_for_status()
        else:
            response.raise_for_status()
            size = response.json().get("result", {}).get("config", {}).get("params", {}).get("vectors", {}).get("size")
            if size is not None and int(size) != self.dimensions:
                raise RuntimeError("Qdrant collection has an incompatible vector size")
        self.ready = True

    async def index_message(self, room: str, uid: str, name: str, content: str):
        if not self.enabled:
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
                json={"points": [{"id": str(uuid.uuid4()), "vector": vector,
                    "payload": {"room_id": room, "uid": uid, "name": name[:80],
                    "content": text, "created_at": now,
                    "expires_at": now + self.retention_days * 86400}}]},
            )
            response.raise_for_status()
        except Exception:
            log.exception("Vector message indexing failed")

    async def search(self, room: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
        if not self.enabled or not query.strip():
            return []
        try:
            vector = await self._embed(query, query=True)
            await self.ensure_collection()
            client = await self._http()
            response = await client.post(
                f"{self.url}/collections/{self.collection}/points/search",
                headers=self._headers(),
                json={"vector": vector, "limit": max(1, min(8, limit)), "with_payload": True,
                    "filter": {"must": [
                        {"key": "room_id", "match": {"value": room}},
                        {"key": "expires_at", "range": {"gt": int(time.time())}}]}},
            )
            response.raise_for_status()
            return [p.get("payload", {}) for p in response.json().get("result", [])
                    if p.get("payload", {}).get("content")]
        except Exception:
            log.exception("Vector memory search failed")
            return []

    async def forget_user(self, room: str, uid: str):
        if not self.enabled:
            return
        try:
            await self.ensure_collection()
            client = await self._http()
            response = await client.post(
                f"{self.url}/collections/{self.collection}/points/delete?wait=true",
                headers=self._headers(),
                json={"filter": {"must": [
                    {"key": "room_id", "match": {"value": room}},
                    {"key": "uid", "match": {"value": uid}}]}},
            )
            response.raise_for_status()
        except Exception:
            log.exception("Vector memory deletion failed")

    async def cleanup(self):
        if not self.enabled or time.time() - self.last_cleanup < self.cleanup_interval:
            return
        try:
            await self.ensure_collection()
            client = await self._http()
            response = await client.post(
                f"{self.url}/collections/{self.collection}/points/delete?wait=true",
                headers=self._headers(),
                json={"filter": {"must": [
                    {"key": "expires_at", "range": {"lt": int(time.time())}}]}},
            )
            response.raise_for_status()
            self.last_cleanup = time.time()
        except Exception:
            log.exception("Qdrant retention cleanup failed")
