from __future__ import annotations

import asyncio
import time

import pytest

from stella.vector_memory import VectorMemory


@pytest.fixture
def memory(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "https://qdrant.example")
    monkeypatch.setenv("QDRANT_API_KEY", "qdrant-test")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
    monkeypatch.setenv("GEMINI_EMBEDDING_DIMENSIONS", "768")
    return VectorMemory()


@pytest.mark.asyncio
async def test_embedding_cache_reuses_vector_without_retaining_plaintext(memory):
    calls = 0

    async def embed(text, query):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return [0.25] * 768

    memory._embed_uncached = embed
    first = await memory._embed("contexto de prueba")
    second = await memory._embed("contexto de prueba")

    assert first == second
    assert calls == 1
    assert memory.metrics["embedding_cache_hits"] == 1
    assert all("contexto" not in key for key in memory._embedding_cache)


@pytest.mark.asyncio
async def test_concurrent_identical_embedding_requests_are_coalesced(memory):
    calls = 0
    gate = asyncio.Event()

    async def embed(text, query):
        nonlocal calls
        calls += 1
        await gate.wait()
        return [0.5] * 768

    memory._embed_uncached = embed
    first = asyncio.create_task(memory._embed("consulta simultanea"))
    second = asyncio.create_task(memory._embed("consulta simultanea"))
    await asyncio.sleep(0)
    gate.set()
    a, b = await asyncio.gather(first, second)

    assert a == b
    assert calls == 1
    assert memory.metrics["embedding_coalesced"] == 1


def test_embedding_cache_key_separates_query_and_document_profiles(memory):
    document = memory._embedding_cache_key("mismo texto", False)
    query = memory._embedding_cache_key("mismo texto", True)
    assert document != query


def test_vector_memory_reports_gemini_and_qdrant_failures_independently(memory):
    memory.embedding_blocked_status = 403
    assert "Gemini Embeddings pausado" in memory.status
    assert "Qdrant configurado" in memory.status

    memory.embedding_blocked_status = None
    memory.qdrant_blocked_status = 401
    assert "Qdrant pausado" in memory.status
    assert "Gemini Embeddings listo" in memory.status


def test_semantic_message_filter_rejects_secrets_contacts_and_sensitive_disclosures():
    assert VectorMemory._is_private_or_secret("mi token=super-secret")
    assert VectorMemory._is_private_or_secret("mi correo es persona@example.com")
    assert VectorMemory._is_private_or_secret("mi diagnóstico es ansiedad")
    assert not VectorMemory._is_private_or_secret("Estoy desarrollando un juego de fantasía")


def test_point_ids_are_deterministic_but_scoped_by_member_and_day():
    first = VectorMemory._point_id("room", "member", "same message", 20000)
    same = VectorMemory._point_id("room", "member", "same message", 20000)
    other_user = VectorMemory._point_id("room", "other", "same message", 20000)
    next_day = VectorMemory._point_id("room", "member", "same message", 20001)

    assert first == same
    assert first != other_user
    assert first != next_day


def test_score_threshold_is_bounded_and_has_safe_fallback(monkeypatch):
    monkeypatch.setenv("QDRANT_SCORE_THRESHOLD", "9")
    memory = VectorMemory()
    assert memory.score_threshold == 1.0

    monkeypatch.setenv("QDRANT_SCORE_THRESHOLD", "not-a-number")
    memory = VectorMemory()
    assert memory.score_threshold == 0.35
