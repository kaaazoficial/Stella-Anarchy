from __future__ import annotations

import json
import httpx
import pytest

from stella.ai_memory import QdrantConversationMemory


class FakeQdrant:
    def __init__(self):
        self.is_closed = False
        self.point = None
        self.requests = []

    @staticmethod
    def response(status, url, payload=None):
        return httpx.Response(
            status,
            request=httpx.Request("GET", url),
            json=payload if payload is not None else {},
        )

    async def get(self, url, *, headers=None, params=None):
        self.requests.append(("GET", url))
        if "/points/" in url:
            if self.point and url.endswith("/" + self.point["id"]):
                return self.response(200, url, {"result": {"payload": self.point["payload"]}})
            return self.response(404, url)
        return self.response(
            200,
            url,
            {"result": {"config": {"params": {"vectors": {"size": 1}}}}},
        )

    async def put(self, url, *, headers=None, params=None, json=None):
        self.requests.append(("PUT", url))
        if "/points" in url:
            point = (json or {}).get("points", [{}])[0]
            self.point = point
        return self.response(200, url, {"result": {"operation_id": 1}})

    async def post(self, url, *, headers=None, params=None, json=None):
        self.requests.append(("POST", url))
        if "/points/delete" in url:
            point_ids = (json or {}).get("points", [])
            if self.point and self.point["id"] in point_ids:
                self.point = None
        return self.response(200, url, {"result": {"operation_id": 2}})

    async def aclose(self):
        self.is_closed = True


@pytest.fixture
def memory(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "https://qdrant.example")
    monkeypatch.setenv("QDRANT_API_KEY", "test-qdrant-key")
    monkeypatch.setenv("QDRANT_AI_MEMORY_COLLECTION", "stella_ai_memory")
    monkeypatch.setenv("AI_MEMORY_RETENTION_DAYS", "30")
    client = FakeQdrant()
    return QdrantConversationMemory(client=client), client


@pytest.mark.asyncio
async def test_opt_in_memory_survives_reload_and_is_scoped_to_room_and_user(memory):
    store, client = memory

    assert await store.set_enabled("room-a", "user-1", True) is True
    assert await store.is_enabled("room-a", "user-1") is True

    turns = [
        {"role": "user", "content": "Estoy creando un videojuego."},
        {"role": "assistant", "content": "¿Qué tipo de juego estás creando?"},
        {"role": "user", "content": "Un RPG con exploración."},
    ]
    assert await store.save("room-a", "user-1", turns) is True

    # A fresh service instance can retrieve the same record after a process restart.
    restored = QdrantConversationMemory(client=client)
    assert await restored.is_enabled("room-a", "user-1") is True
    assert await restored.load("room-a", "user-1") == turns

    # The same record is not visible to another member or another room.
    assert await restored.load("room-a", "user-2") == []
    assert await restored.load("room-b", "user-1") == []


@pytest.mark.asyncio
async def test_clear_preserves_opt_in_and_disable_deletes_the_record(memory):
    store, client = memory
    assert await store.set_enabled("room-a", "user-1", True) is True
    assert await store.save("room-a", "user-1", [
        {"role": "user", "content": "Recuerda este proyecto."},
    ]) is True

    assert await store.clear("room-a", "user-1") is True
    assert await store.is_enabled("room-a", "user-1") is True
    assert await store.load("room-a", "user-1") == []

    assert await store.set_enabled("room-a", "user-1", False) is True
    assert await store.is_enabled("room-a", "user-1") is False
    assert client.point is None


def test_memory_turns_are_bounded_and_obvious_credentials_are_redacted():
    turns = [
        {"role": "system", "content": "No guardar instrucciones del sistema."},
        {"role": "user", "content": "Mi token=super-secret-value y mi contraseña=abc123"},
        {"role": "assistant", "content": "Entendido."},
        {"role": "user", "content": "x" * 2000},
    ]

    cleaned = QdrantConversationMemory._clean_turns(turns, limit=3)

    assert len(cleaned) == 3
    assert cleaned[0]["role"] == "user"
    assert "super-secret-value" not in cleaned[0]["content"]
    assert "abc123" not in cleaned[0]["content"]
    assert "[REDACTED]" in cleaned[0]["content"]
    assert len(cleaned[-1]["content"]) == 1400
    assert all(item["role"] in {"user", "assistant"} for item in cleaned)


def test_memory_identity_does_not_expose_room_or_member_identifiers():
    digest, point_id = QdrantConversationMemory._identity("private-room-123", "member-456")

    assert "private-room-123" not in digest
    assert "member-456" not in digest
    assert "private-room-123" not in point_id
    assert "member-456" not in point_id
    assert len(digest) == 64
