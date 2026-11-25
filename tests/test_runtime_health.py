from __future__ import annotations

import asyncio
from types import SimpleNamespace

import main


def test_health_reports_component_status_without_exposing_secrets(monkeypatch):
    monkeypatch.setattr(
        main,
        "verify_sqlite_database",
        lambda path: {"ok": True, "integrity": "ok"},
    )
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(
            database_path="unused",
            version="test",
            bot_token="private-token",
            webhook_secret="private-secret",
        ),
    )
    monkeypatch.setattr(
        main,
        "bot",
        SimpleNamespace(vector_memory=SimpleNamespace(status="conectada")),
    )
    monkeypatch.setenv("GEMINI_API_KEY", "private-ai-key")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    result = asyncio.run(main.health())

    assert result["ok"] is True
    assert result["status"] == "healthy"
    assert result["checks"]["database"] == "ok"
    assert result["checks"]["anarchy_token_configured"] is True
    assert result["checks"]["webhook_secret_configured"] is True
    assert result["checks"]["ai_provider_configured"] is True
    assert result["checks"]["vector_memory"] == "conectada"
    rendered = repr(result)
    assert "private-token" not in rendered
    assert "private-secret" not in rendered
    assert "private-ai-key" not in rendered


def test_readiness_requires_platform_credentials_and_healthy_database(monkeypatch):
    monkeypatch.setattr(
        main,
        "verify_sqlite_database",
        lambda path: {"ok": True, "integrity": "ok"},
    )
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(
            database_path="unused",
            bot_token="configured",
            webhook_secret="configured",
        ),
    )

    response = asyncio.run(main.readiness())

    assert response.status_code == 200
    assert response.body == b'{"ready":true,"missing":[]}'


def test_readiness_reports_missing_credentials_without_values(monkeypatch):
    monkeypatch.setattr(
        main,
        "verify_sqlite_database",
        lambda path: {"ok": False, "integrity": "failed"},
    )
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(
            database_path="unused",
            bot_token="",
            webhook_secret="",
        ),
    )

    response = asyncio.run(main.readiness())

    assert response.status_code == 503
    assert b"ANARCHY_BOT_TOKEN" in response.body
    assert b"ANARCHY_WEBHOOK_SECRET" in response.body
    assert b"database" in response.body
    assert b"configured" not in response.body
