from __future__ import annotations

import json
import time
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
            body = json or {}
            point_ids = body.get("points", [])
            if self.point and self.point["id"] in point_ids:
                self.point = None
            must = body.get("filter", {}).get("must", [])
            if self.point and any(
                item.get("key") == "expires_at"
                and self.point["payload"].get("expires_at", 0) < item.get("range", {}).get("lt", 0)
                for item in must
            ):
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
    assert client.point is not None
    assert client.point["payload"]["enabled"] is False
    assert json.loads(client.point["payload"]["turns"]) == []


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



@pytest.mark.asyncio
async def test_expired_memory_is_not_restored_and_is_removed(memory):
    store, client = memory
    assert await store.set_enabled("room-a", "user-1", True) is True
    assert await store.save("room-a", "user-1", [
        {"role": "user", "content": "Este contexto debe caducar."},
    ]) is True
    client.point["payload"]["expires_at"] = 1
    store._last_cleanup = 0.0

    assert await store.is_enabled("room-a", "user-1") is False
    assert await store.load("room-a", "user-1") == []
    assert client.point is None



def test_explicit_fact_capture_and_stable_personal_context():
    samples = {
        "Stella recuerda que mi proyecto se llama Eden Veil": "Eden Veil",
        "Mi objetivo es estudiar informática": "estudiar informática",
        "Estoy creando un videojuego de exploración": "un videojuego de exploración",
        "Prefiero respuestas breves y directas": "respuestas breves y directas",
    }
    for message, expected in samples.items():
        facts = QdrantConversationMemory.extract_explicit_facts(message)
        assert facts
        assert expected.casefold() in facts[0]["content"].casefold()


def test_sensitive_and_credential_facts_are_not_saved():
    rejected = (
        "Recuerda que mi contraseña=secret123",
        "Recuerda que mi domicilio es calle falsa 123",
        "Recuerda que mi diagnóstico es ansiedad",
        "Recuerda que mi correo es persona@example.com",
        "Recuerda que mi número es +56912345678",
        "Recuerda que mi cuenta está en https://example.com",
    )
    for message in rejected:
        assert QdrantConversationMemory.extract_explicit_facts(message) == []


@pytest.mark.asyncio
async def test_durable_facts_survive_transcript_clear_and_can_be_deleted(memory):
    store, client = memory
    assert await store.set_enabled("room-a", "user-1", True) is True
    fact_id = await store.remember_fact(
        "room-a", "user-1", "Estoy desarrollando un proyecto de fantasía.", topic="proyecto"
    )
    assert fact_id
    assert len(await store.load_facts("room-a", "user-1")) == 1
    assert await store.save("room-a", "user-1", [
        {"role": "user", "content": "Seguimos con el mismo proyecto."},
    ]) is True

    # Clearing only the recent transcript must not remove durable facts.
    assert await store.clear("room-a", "user-1") is True
    assert await store.load("room-a", "user-1") == []
    facts = await store.load_facts("room-a", "user-1")
    assert len(facts) == 1
    assert facts[0]["id"] == fact_id

    assert await store.forget_fact("room-a", "user-1", fact_id) is True
    assert await store.load_facts("room-a", "user-1") == []


@pytest.mark.asyncio
async def test_new_members_get_automatic_memory_without_commands(memory):
    store, _ = memory
    assert await store.is_enabled("room-a", "user-1") is True
    turns = [
        {"role": "user", "content": "Estoy trabajando en un proyecto de fantasía."},
        {"role": "assistant", "content": "Cuéntame más."},
    ]
    assert await store.save("room-a", "user-1", turns) is True
    assert await store.load("room-a", "user-1") == turns


@pytest.mark.asyncio
async def test_facts_are_remembered_automatically_without_save_command(memory):
    store, _ = memory
    assert await store.is_enabled("room-a", "user-1") is True
    saved = await store.remember_from_message(
        "room-a", "user-1", "Estoy creando un mundo de fantasía llamado Elaria"
    )
    assert len(saved) == 1
    facts = await store.load_facts("room-a", "user-1")
    assert len(facts) == 1
    assert "Elaria" in facts[0]["content"]


@pytest.mark.asyncio
async def test_explicit_opt_out_remains_disabled_without_commands_after_restart(memory):
    store, client = memory
    assert await store.set_enabled("room-a", "user-1", False) is True
    restored = QdrantConversationMemory(client=client)
    assert await restored.is_enabled("room-a", "user-1") is False
    assert await restored.save("room-a", "user-1", [
        {"role": "user", "content": "No se debe guardar tras desactivar."},
    ]) is False


@pytest.mark.asyncio
async def test_periodic_cleanup_is_exposed_for_background_worker(memory):
    store, client = memory
    await store.cleanup()
    assert any(method == "POST" and url.endswith("/points/delete?wait=true")
               for method, url in client.requests)


@pytest.mark.asyncio
async def test_opt_out_erases_both_recent_turns_and_durable_facts(memory):
    store, _ = memory
    assert await store.set_enabled("room-a", "user-1", True) is True
    assert await store.save("room-a", "user-1", [
        {"role": "user", "content": "Contexto temporal."},
    ]) is True
    assert await store.remember_fact("room-a", "user-1", "Mi proyecto es Atlas", topic="proyecto")
    assert await store.set_enabled("room-a", "user-1", False) is True
    assert await store.load("room-a", "user-1") == []
    assert await store.load_facts("room-a", "user-1") == []
