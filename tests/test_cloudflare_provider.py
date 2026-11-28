from __future__ import annotations

import asyncio

from stella.ai_core import StellaAICore


def test_cloudflare_is_last_fallback_after_gemini_groq_and_zai(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_AI_GATEWAY_TOKEN", "gateway-test-secret")
    monkeypatch.delenv("STELLA_AI_PRIMARY_PROVIDER", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "gemini-test")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test")
    monkeypatch.setenv("ZAI_API_KEY", "zai-test")

    core = StellaAICore()

    assert core._primary_provider_config()["name"] == "Google Gemini"
    assert [item["name"] for item in core._fallback_provider_configs()] == [
        "Groq", "Z.AI", "Cloudflare Workers AI",
    ]
    diagnostics = core.provider_diagnostics()
    assert diagnostics["cloudflare_configured"] is True
    assert "gateway-test-secret" not in repr(diagnostics)


def test_cloudflare_gateway_can_be_selected_without_cloudflare_api_key(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_AI_GATEWAY_TOKEN", "shared-secret")
    monkeypatch.setenv("STELLA_AI_PRIMARY_PROVIDER", "cloudflare")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("ZAI_API_KEY", raising=False)

    core = StellaAICore()

    assert core.is_enabled is True
    assert core._primary_provider_config()["url"].endswith("/internal/infer")
    assert core._primary_provider_config()["model"] == "@cf/zai-org/glm-4.7-flash"


def test_cloudflare_is_not_selected_without_gateway_secret(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_AI_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("STELLA_AI_PRIMARY_PROVIDER", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "gemini-test")

    assert StellaAICore()._primary_provider_config()["name"] == "Google Gemini"


def test_cloudflare_gateway_uses_bearer_secret_and_chat_completion_shape(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_AI_GATEWAY_TOKEN", "shared-gateway-secret")
    monkeypatch.setenv("STELLA_AI_PRIMARY_PROVIDER", "cloudflare")
    core = StellaAICore()
    captured = {}

    class FakeResponse:
        status_code = 200
        is_success = True
        headers = {}

        @staticmethod
        def json():
            return {
                "choices": [{
                    "message": {
                        "content": (
                            '{"kind":"answer","answer":"Respuesta desde Cloudflare",'
                            '"command":"","args":"","steps":[]}'
                        )
                    }
                }],
                "usage": {"prompt_tokens": 30, "completion_tokens": 8},
            }

    async def fake_request(method, url, **kwargs):
        captured.update({"method": method, "url": url, **kwargs})
        return FakeResponse()

    result = asyncio.run(
        core._request_json_with_provider(
            "Instrucciones de Stella",
            [{"role": "user", "content": "Hola"}],
            fake_request,
            response_mode="action",
            max_output_tokens=256,
        )
    )

    assert result["answer"] == "Respuesta desde Cloudflare"
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/internal/infer")
    assert captured["headers"]["Authorization"] == "Bearer shared-gateway-secret"
    assert captured["json"]["model"] == "@cf/zai-org/glm-4.7-flash"
    assert captured["json"]["messages"][0] == {
        "role": "system", "content": "Instrucciones de Stella"
    }
    assert captured["json"]["max_completion_tokens"] == 256
    assert captured["json"]["response_format"] == {"type": "json_object"}


def test_cloudflare_is_attempted_only_after_gemini_groq_and_zai_fail(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_AI_GATEWAY_TOKEN", "shared-secret")
    monkeypatch.setenv("GOOGLE_API_KEY", "gemini-test")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test")
    monkeypatch.setenv("ZAI_API_KEY", "zai-test")
    monkeypatch.delenv("STELLA_AI_PRIMARY_PROVIDER", raising=False)
    core = StellaAICore()
    attempted = []

    async def fake_provider_request(*args, **kwargs):
        provider = core._active_provider_config()["name"]
        attempted.append(provider)
        if provider != "Cloudflare Workers AI":
            raise RuntimeError(f"simulated {provider} outage")
        return {
            "kind": "answer",
            "answer": "Respuesta desde Cloudflare",
            "command": "",
            "args": "",
            "steps": [],
        }

    core._request_json_with_provider = fake_provider_request
    result = asyncio.run(
        core._request_json(
            "Instrucciones",
            [{"role": "user", "content": "Consulta"}],
            object(),
        )
    )

    assert attempted == [
        "Google Gemini", "Groq", "Z.AI", "Cloudflare Workers AI",
    ]
    assert result["answer"] == "Respuesta desde Cloudflare"
    assert core._last_provider_summary["name"] == "Cloudflare Workers AI"
    assert core.metrics["ai_provider_failovers"] == 1
