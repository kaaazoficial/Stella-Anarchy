import asyncio
import time

import pytest

from stella.response_style import (
    build_response_mode_guidance,
    classify_response_style,
)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Me siento fatal y necesito desahogarme", "emotional"),
        ("Mi aplicación no inicia y aparece un error", "troubleshooting"),
        ("Compara PostgreSQL y MySQL", "comparison"),
        ("¿Qué me recomiendas para empezar?", "advice"),
        ("Invéntame una criatura para mi mundo", "creative"),
        ("Explícame cómo funciona una batería", "explanation"),
        ("Hola, ¿qué tal?", "direct"),
        ("Jajaja", "social"),
        ("Qué buena idea", "conversation"),
    ],
)
def test_current_message_selects_a_matching_response_style(message, expected):
    assert classify_response_style(message) == expected


def test_roleplay_has_priority_over_generic_short_message_classification():
    assert classify_response_style("*Stella abre la puerta.*") == "roleplay"
    guidance = build_response_mode_guidance("*Stella abre la puerta.*")
    assert "MODO DE RESPUESTA: ROLEPLAY" in guidance
    assert "no controles al otro personaje" in guidance


def test_playful_replies_use_their_own_style():
    assert classify_response_style("Stella, qué idiota", intent="playful") == "playful"
    guidance = build_response_mode_guidance("Stella, qué idiota", intent="playful")
    assert "específica respecto al mensaje" in guidance
    assert "respuestas anteriores" in guidance


def test_response_guidance_is_concise_and_not_a_fixed_reply_template():
    for message in (
        "Hola",
        "Explícame qué es la electricidad",
        "Estoy preocupado por un examen",
        "No funciona mi programa",
        "Compara dos alternativas",
        "Escribe una escena",
    ):
        guidance = build_response_mode_guidance(message)
        assert guidance.startswith("MODO DE RESPUESTA:")
        assert len(guidance) < 700
        assert "responde exactamente" not in guidance.casefold()


def test_ai_core_conversation_prompt_includes_request_specific_style():
    from stella.ai_core import build_ai_instructions

    prompt = build_ai_instructions(
        frozenset(), "conversation", request_text="Explícame qué es la electricidad"
    )
    assert "MODO DE RESPUESTA: EXPLANATION" in prompt
    assert "Responde primero la pregunta central" in prompt


def test_specialized_research_prompt_uses_evidence_first_guidance():
    guidance = build_response_mode_guidance(
        "Investiga las causas del apagón", intent="research"
    )
    assert "MODO DE RESPUESTA: RESEARCH" in guidance
    assert "hallazgo principal" in guidance


def test_ai_utility_path_keeps_all_shared_persona_guidance(monkeypatch):
    from stella.ai import STELLA_SYSTEM, StellaAI

    ai = StellaAI()
    ai.gemini_key = "test-key"
    captured = {}

    async def fake_gemini(prompt, system):
        captured["prompt"] = prompt
        captured["system"] = system
        return "La electricidad es el movimiento de cargas."

    monkeypatch.setattr(ai, "_ask_gemini", fake_gemini)

    answer = asyncio.run(ai.ask("Explícame qué es la electricidad"))

    assert answer == "La electricidad es el movimiento de cargas."
    assert len(STELLA_SYSTEM) > 3000
    assert len(captured["system"]) > 3000
    assert "CALIDAD Y NATURALIDAD" in captured["system"]
    assert "DINÁMICA DE CHAT GRUPAL" in captured["system"]
    assert "MODO DE RESPUESTA: EXPLANATION" in captured["system"]


def test_ai_utility_path_includes_style_only_anti_repetition_context(monkeypatch):
    from stella.ai import StellaAI

    ai = StellaAI()
    ai.gemini_key = "test-key"
    ai._memory["room:user"] = {
        "turns": [
            {"role": "assistant", "content": "¡Claro! Vamos por partes."},
            {"role": "user", "content": "Cuéntame un dato."},
            {"role": "assistant", "content": "Por supuesto, aquí tienes el dato."},
        ],
        "updated_at": time.monotonic(),
    }
    captured = {}

    async def fake_gemini(prompt, system):
        captured["system"] = system
        return "La respuesta concreta."

    monkeypatch.setattr(ai, "_ask_gemini", fake_gemini)

    asyncio.run(ai.ask("Explícame qué es una estrella", session_key="room:user"))

    assert "REFERENCIAS ANTIRREPETICIÓN" in captured["system"]
    assert "¡Claro! Vamos por partes." in captured["system"]
    assert "solo estilo" in captured["system"]
