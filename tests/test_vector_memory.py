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
