from __future__ import annotations

import asyncio

from stella.ai_core import StellaAICore


def test_zai_is_the_third_provider_after_gemini_and_groq(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "gemini-test")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test")
    monkeypatch.setenv("ZAI_API_KEY", "zai-test")
    monkeypatch.delenv("STELLA_AI_PRIMARY_PROVIDER", raising=False)

    core = StellaAICore()

    assert core._primary_provider_config()["name"] == "Google Gemini"
    assert [item["name"] for item in core._fallback_provider_configs()] == [
        "Groq",
        "Z.AI",
    ]
    diagnostics = core.provider_diagnostics()
    assert diagnostics["zai_configured"] is True
    assert "zai-test" not in repr(diagnostics)


def test_zai_is_available_when_gemini_and_groq_are_not_configured(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("ZAI_API_KEY", "zai-test")
    monkeypatch.delenv("STELLA_AI_PRIMARY_PROVIDER", raising=False)

    core = StellaAICore()

    assert core._primary_provider_config()["name"] == "Z.AI"
    assert core.is_enabled is True
    assert core._primary_provider_config()["model"] == "glm-4.7-flash"


def test_zai_chat_completions_uses_openai_compatible_request_and_response():
    core = StellaAICore()
    config = {
        "name": "Z.AI",
        "key": "zai-unit-test-secret",
        "url": "https://api.z.ai/api/paas/v4",
        "model": "glm-4.7-flash",
    }
    token = core._provider_override.set(config)
    captured = {}

    class FakeResponse:
        status_code = 200
        is_success = True
        headers = {}

        @staticmethod
        def json():
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"kind":"answer","answer":"Hola",'
                                '"command":"","args":"","steps":[]}'
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 5},
            }

    async def fake_request(method, url, **kwargs):
        captured.update({"method": method, "url": url, **kwargs})
        return FakeResponse()

    try:
        result = asyncio.run(
            core._request_json_with_provider(
                "Instrucciones de prueba",
                [{"role": "user", "content": "Hola"}],
                fake_request,
                response_mode="answer",
                max_output_tokens=256,
            )
        )
    finally:
        core._provider_override.reset(token)

    assert result["answer"] == "Hola"
    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.z.ai/api/paas/v4/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer zai-unit-test-secret"
    body = captured["json"]
    assert body["model"] == "glm-4.7-flash"
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][-1] == {"role": "user", "content": "Hola"}
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 256


def test_zai_fallback_is_attempted_after_gemini_and_groq_fail(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "gemini-test")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test")
    monkeypatch.setenv("ZAI_API_KEY", "zai-test")
    monkeypatch.delenv("STELLA_AI_PRIMARY_PROVIDER", raising=False)
    core = StellaAICore()
    attempted = []

    async def fake_provider_request(*args, **kwargs):
        provider = core._active_provider_config()["name"]
        attempted.append(provider)
        if provider != "Z.AI":
            raise RuntimeError("simulated provider outage")
        return {
            "kind": "answer",
            "answer": "Respuesta desde Z.AI",
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

    assert attempted == ["Google Gemini", "Groq", "Z.AI"]
    assert result["answer"] == "Respuesta desde Z.AI"
    assert core._last_provider_summary["name"] == "Z.AI"
    assert core.metrics["ai_provider_failovers"] == 1


def test_zai_parser_joins_multiblock_content_with_newlines():
    result = StellaAICore._extract_zai_chat_completion_text(
        {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "Primera parte"},
                            {"type": "text", "text": "Segunda parte"},
                        ]
                    }
                }
            ]
        }
    )

    assert result == "Primera parte\nSegunda parte"
