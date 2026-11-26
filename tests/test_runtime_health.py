from __future__ import annotations

import asyncio
import json
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
        SimpleNamespace(
            vector_memory=SimpleNamespace(status="conectada"),
            ai_core=SimpleNamespace(provider_diagnostics=lambda: {
                "primary": "Google Gemini",
                "gemini_configured": True,
                "groq_configured": True,
                "last_successful_provider": "Google Gemini",
                "gemini_successes": 4,
                "gemini_failures": 0,
                "groq_successes": 0,
                "groq_failures": 0,
                "failovers": 0,
            }),
        ),
    )
    monkeypatch.setenv("GEMINI_API_KEY", "private-ai-key")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    response = asyncio.run(main.health())
    result = json.loads(response.body)

    assert response.status_code == 200
    assert result["ok"] is True
    assert result["status"] == "healthy"
    assert result["checks"]["database"] == "ok"
    assert result["checks"]["anarchy_token_configured"] is True
    assert result["checks"]["webhook_secret_configured"] is True
    assert result["checks"]["ai_provider_configured"] is True
    assert result["checks"]["ai_providers"]["primary"] == "Google Gemini"
    assert result["checks"]["ai_providers"]["last_successful_provider"] == "Google Gemini"
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



def test_health_returns_service_unavailable_when_database_is_unhealthy(monkeypatch):
    monkeypatch.setattr(
        main,
        "verify_sqlite_database",
        lambda path: {"ok": False, "integrity": "database disk image is malformed"},
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
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    response = asyncio.run(main.health())
    result = json.loads(response.body)

    assert response.status_code == 503
    assert result["ok"] is False
    assert result["status"] == "unhealthy"
    assert result["checks"]["database"] == "database disk image is malformed"
    assert "private-token" not in response.body.decode()
    assert "private-secret" not in response.body.decode()



def test_root_head_endpoint_returns_success():
    response = asyncio.run(main.root_head())
    assert response.status_code == 200
    assert response.body == b""


def test_health_head_endpoint_reflects_health_status(monkeypatch):
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
            bot_token="configured",
            webhook_secret="configured",
        ),
    )
    monkeypatch.setattr(
        main,
        "bot",
        SimpleNamespace(vector_memory=None),
    )
    response = asyncio.run(main.health_head())
    assert response.status_code == 200
    assert response.body == b""


def test_health_head_returns_503_when_database_is_unhealthy(monkeypatch):
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
            version="test",
            bot_token="configured",
            webhook_secret="configured",
        ),
    )
    monkeypatch.setattr(
        main,
        "bot",
        SimpleNamespace(vector_memory=None),
    )
    response = asyncio.run(main.health_head())
    assert response.status_code == 503
    assert response.body == b""



def test_public_api_docs_are_disabled_by_default():
    assert main.app.docs_url is None
    assert main.app.redoc_url is None
    assert main.app.openapi_url is None
