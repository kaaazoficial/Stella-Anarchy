from __future__ import annotations

import asyncio

import pytest

from stella.ai import StellaAI
from stella.music_voice import (
    MusicVoice,
    TTS_VOICES,
    _allowed_audio_url,
    _license_label,
    _safe_filename,
)


def test_stella_models_use_configured_defaults(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test")
    ai = StellaAI()
    assert ai.gemini_model == "gemini-3.5-flash-lite"
    assert ai.groq_model == "openai/gpt-oss-20b"
    assert ai.primary == "gemini"


@pytest.mark.asyncio
async def test_groq_is_used_when_gemini_fails(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test")
    ai = StellaAI()

    async def fail_gemini(prompt, system):
        raise RuntimeError("Gemini unavailable")

    async def groq_answer(prompt, system):
        return "Respuesta de respaldo."

    ai._ask_gemini = fail_gemini
    ai._ask_groq = groq_answer
    assert await ai.ask("Hola") == "Respuesta de respaldo."
    assert ai.failures["gemini"] == 1
    await ai.close()


def test_music_sources_are_restricted_to_supported_https_hosts():
    assert _allowed_audio_url("https://archive.org/download/item/audio.mp3")
    assert _allowed_audio_url("https://ia800000.us.archive.org/file.mp3")
    assert _allowed_audio_url("https://www.jamendo.com/track/123")
    assert not _allowed_audio_url("http://archive.org/download/item/audio.mp3")
    assert not _allowed_audio_url("https://youtube.com/watch?v=abc")
    assert not _allowed_audio_url("https://user:pass@archive.org/audio.mp3")


def test_music_license_filter_requires_open_license():
    assert _license_label("https://creativecommons.org/licenses/by/4.0/") == "Creative Commons BY"
    assert _license_label("https://creativecommons.org/licenses/by-sa/4.0/") == "Creative Commons BY-SA"
    assert _license_label("https://creativecommons.org/publicdomain/zero/1.0/") == "CC0"
    assert _license_label("https://creativecommons.org/licenses/by-nc/4.0/") is None
    assert _license_label("") is None


def test_music_filename_validation_blocks_paths_and_unknown_types():
    assert _safe_filename("music.mp3") == "music.mp3"
    assert _safe_filename("../secret.mp3") is None
    assert _safe_filename("C:\\secret.mp3") is None
    assert _safe_filename("poster.jpg") is None


def test_spanish_voice_options_are_available():
    assert TTS_VOICES["catalina"] == "es-CL-CatalinaNeural"
    assert TTS_VOICES["carlos"] == "es-CO-GonzaloNeural"


@pytest.mark.asyncio
async def test_voice_room_refuses_to_join_without_an_active_call():
    class FakePlatform:
        async def request(self, method, path):
            assert method == "GET"
            assert path == "/live-rooms"
            return {"ok": True, "status": 200, "data": {"roomList": []}}

    voice = MusicVoice(FakePlatform())
    ok, message = await voice.ensure_room("thread-1")
    assert not ok
    assert "No hay una llamada de voz activa" in message
    await voice.close()
