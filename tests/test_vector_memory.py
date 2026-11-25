from __future__ import annotations

import httpx
import pytest

from stella.vector_memory import VectorMemory


class UnauthorizedGeminiClient:
    def __init__(self):
        self.calls = 0
        self.is_closed = False

    async def post(self, url, *, headers=None, json=None):
        self.calls += 1
        request = httpx.Request("POST", url)
        return httpx.Response(401, request=request, json={"error": {"message": "invalid key"}})

    async def aclose(self):
        self.is_closed = True


@pytest.mark.asyncio
async def test_gemini_auth_failure_opens_circuit_and_stops_repeated_requests(monkeypatch, caplog):
    monkeypatch.setenv("QDRANT_URL", "https://qdrant.example")
    monkeypatch.setenv("QDRANT_API_KEY", "qdrant-test")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")

    memory = VectorMemory()
    fake_client = UnauthorizedGeminiClient()
    memory.client = fake_client

    await memory.index_message("room-1", "user-1", "Persona", "Este es un mensaje de prueba.")
    await memory.index_message("room-1", "user-1", "Persona", "Otro mensaje de prueba.")
    results = await memory.search("room-1", "consulta semántica")

    assert fake_client.calls == 1
    assert results == []
    assert memory.embedding_blocked_status == 401
    assert "HTTP 401" in memory.status
    assert "invalid key" not in caplog.text.casefold()
    await memory.close()


@pytest.mark.asyncio
async def test_missing_credentials_make_vector_memory_a_noop(monkeypatch):
    monkeypatch.delenv("QDRANT_URL", raising=False)
    monkeypatch.delenv("QDRANT_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    memory = VectorMemory()
    await memory.index_message("room-1", "user-1", "Persona", "Mensaje suficientemente largo.")
    assert memory.status.startswith("sin configurar")
    assert memory.client is None


class QdrantCollectionClient:
    def __init__(self):
        self.is_closed = False
        self.indexes = []

    async def get(self, url, *, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            request=request,
            json={
                "result": {
                    "config": {"params": {"vectors": {"size": 768}}},
                    "payload_schema": {},
                }
            },
        )

    async def put(self, url, *, headers=None, json=None):
        request = httpx.Request("PUT", url)
        self.indexes.append((url, json))
        return httpx.Response(200, request=request, json={"result": {"operation_id": 1}})

    async def aclose(self):
        self.is_closed = True


@pytest.mark.asyncio
async def test_qdrant_creates_payload_indexes_required_by_room_and_expiry_filters(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "https://qdrant.example")
    monkeypatch.setenv("QDRANT_API_KEY", "qdrant-test")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
    monkeypatch.setenv("GEMINI_EMBEDDING_DIMENSIONS", "768")

    memory = VectorMemory()
    fake_client = QdrantCollectionClient()
    memory.client = fake_client

    await memory.ensure_collection()

    indexes = {item[1]["field_name"]: item[1]["field_schema"] for item in fake_client.indexes}
    assert indexes == {
        "room_id": "keyword",
        "uid": "keyword",
        "expires_at": "integer",
    }
    assert memory.ready is True
    await memory.close()
