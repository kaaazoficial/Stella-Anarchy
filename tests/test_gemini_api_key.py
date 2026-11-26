from __future__ import annotations

from stella.ai import StellaAI
from stella.ai_core import StellaAICore
from stella.config import get_gemini_api_key
from stella.vector_memory import VectorMemory


def test_google_api_key_takes_precedence_over_gemini_api_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "google-primary-test")
    monkeypatch.setenv("GEMINI_API_KEY", "old-gemini-test")

    assert get_gemini_api_key() == "google-primary-test"
    assert VectorMemory().gemini_key == "google-primary-test"
    assert StellaAI().gemini_key == "google-primary-test"

    core = object.__new__(StellaAICore)
    assert core._gemini_provider_config()["key"] == "google-primary-test"


def test_gemini_api_key_remains_supported_as_fallback(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-fallback-test")

    assert get_gemini_api_key() == "gemini-fallback-test"
    assert VectorMemory().gemini_key == "gemini-fallback-test"
    assert StellaAI().gemini_key == "gemini-fallback-test"


def test_blank_google_api_key_falls_back_to_gemini_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "   ")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-fallback-test")

    assert get_gemini_api_key() == "gemini-fallback-test"
