from __future__ import annotations

import logging
import os
import re
import time
from typing import Any
from urllib.parse import quote

import httpx

from stella.config import get_gemini_api_key
from stella.persona import (
    GENDER_NEUTRAL_GUIDANCE,
    STELLA_SELF_IDENTITY_GUIDANCE,
    STELLA_CHARACTER_GUIDANCE,
    STELLA_GROUP_CHAT_GUIDANCE,
    STELLA_RESPONSE_QUALITY_GUIDANCE,
)

log = logging.getLogger("stella.ai")

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"
DEFAULT_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GROQ_BASE = "https://api.groq.com/openai/v1"

STELLA_SYSTEM = (
    "Eres Stella, la asistente de esta comunidad. "
    + STELLA_SELF_IDENTITY_GUIDANCE
    + "\n\n"
    "Responde en español salvo que te pidan otro idioma. Sé natural, precisa y útil. "
    "No afirmes haber ejecutado acciones que no se hayan realizado. "
    "Distingue hechos de estimaciones y reconoce cuando no sepas algo. "
    "No inventes funciones, fuentes, resultados ni recuerdos. "
    "No prometas recordar todo ni conservar información ilimitadamente; utiliza solo recuerdos autorizados que estén disponibles. "
    + GENDER_NEUTRAL_GUIDANCE
    + "\n\n"
    + STELLA_CHARACTER_GUIDANCE
    + "\n\n"
    + STELLA_RESPONSE_QUALITY_GUIDANCE
    + "\n\n" + STELLA_GROUP_CHAT_GUIDANCE
)


class StellaAI:
    """Gemini-first provider client; durable conversation context is managed separately."""

    def __init__(self) -> None:
        self.gemini_key = get_gemini_api_key()
        self.gemini_model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
        self.gemini_base = os.getenv("GEMINI_API_URL", DEFAULT_GEMINI_BASE).rstrip("/")
        self.groq_key = os.getenv("GROQ_API_KEY", "").strip()
        self.groq_model = os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL).strip()
        self.groq_base = os.getenv("GROQ_API_URL", DEFAULT_GROQ_BASE).rstrip("/")
        # Gemini stays primary whenever its key exists; legacy Groq overrides
        # are ignored so utility calls follow the same provider policy as chat.
        self.primary = "gemini" if self.gemini_key else "groq"
        self.timeout = httpx.Timeout(35.0, connect=8.0)
        self._client: httpx.AsyncClient | None = None
        self.calls = {"gemini": 0, "groq": 0}
        self.failures = {"gemini": 0, "groq": 0}
        # Bounded in-memory context only; never written to SQLite or files.
        self.memory_ttl = 300
        self.memory_turns = 2
        self._memory: dict[str, dict[str, Any]] = {}

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=False)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def _prune_memory(self) -> None:
        now = time.monotonic()
        for key, state in tuple(self._memory.items()):
            if now - float(state.get("updated_at", 0)) > self.memory_ttl:
                self._memory.pop(key, None)
        while len(self._memory) > 100:
            oldest = min(self._memory, key=lambda key: float(self._memory[key].get("updated_at", 0)))
            self._memory.pop(oldest, None)

    def forget_session(self, session_key: str) -> bool:
        return self._memory.pop(str(session_key or ""), None) is not None

    def clear_memory(self) -> None:
        self._memory.clear()

    def status(self) -> str:
        primary = self.primary.capitalize()
        gemini_state = "configurado" if self.gemini_key else "sin clave"
        groq_state = "configurado" if self.groq_key else "sin clave"
        return (
            f"IA principal: {primary}. Gemini: {gemini_state} "
            f"({self.gemini_model}). Groq: {groq_state} ({self.groq_model}). "
            "La memoria persistente de conversación es opcional y se gestiona aparte en Qdrant; "
            "se guarda automáticamente por persona y sala, salvo que la persona la desactive o solicite que olvides sus datos."
        )

    @staticmethod
    def _extract_gemini(payload: dict[str, Any]) -> str:
        candidates = payload.get("candidates") or []
        if not candidates:
            feedback = payload.get("promptFeedback") or {}
            raise RuntimeError(str(feedback.get("blockReason") or "Gemini no devolvió candidatos."))
        parts = ((candidates[0].get("content") or {}).get("parts") or [])
        chunks = [
            str(part.get("text") or "")
            for part in parts
            if isinstance(part, dict) and part.get("text") and not part.get("thought")
        ]
        answer = "\n".join(chunks).strip()
        if not answer:
            raise RuntimeError("Gemini devolvió una respuesta sin texto.")
        return answer

    @staticmethod
    def _extract_groq(payload: dict[str, Any]) -> str:
        direct = str(payload.get("output_text") or "").strip()
        if direct:
            return direct
        chunks: list[str] = []
        for item in payload.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                    text = str(part.get("text") or "").strip()
                    if text:
                        chunks.append(text)
        answer = "\n".join(chunks).strip()
        if not answer:
            raise RuntimeError("Groq devolvió una respuesta sin texto.")
        return answer

    async def _ask_gemini(self, prompt: str, system: str) -> str:
        if not self.gemini_key:
            raise RuntimeError("Gemini no está configurado.")
        client = await self._http()
        url = (
            f"{self.gemini_base}/models/{quote(self.gemini_model, safe='')}:generateContent"
        )
        response = await client.post(
            url,
            headers={"x-goog-api-key": self.gemini_key},
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.6,
                    "maxOutputTokens": 1200,
                    "responseMimeType": "text/plain",
                },
            },
        )
        if not response.is_success:
            log.warning("Gemini request failed with HTTP %s", response.status_code)
            raise RuntimeError(f"Gemini HTTP {response.status_code}")
        self.calls["gemini"] += 1
        return self._extract_gemini(response.json())

    async def _ask_groq(self, prompt: str, system: str) -> str:
        if not self.groq_key:
            raise RuntimeError("Groq no está configurado.")
        client = await self._http()
        url = self.groq_base.rstrip("/")
        if not url.endswith("/responses"):
            url += "/responses"
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {self.groq_key}"},
            json={
                "model": self.groq_model,
                "instructions": system,
                "input": [{"role": "user", "content": prompt}],
                "max_output_tokens": 1200,
                "reasoning": {"effort": "low"},
            },
        )
        if not response.is_success:
            log.warning("Groq request failed with HTTP %s", response.status_code)
            raise RuntimeError(f"Groq HTTP {response.status_code}")
        self.calls["groq"] += 1
        return self._extract_groq(response.json())

    async def ask(
        self, prompt: str, *, system: str | None = None, session_key: str | None = None
    ) -> str:
        text = str(prompt or "").strip()
        if not text:
            return "Escribe una consulta después del comando."
        if len(text) > 6000:
            return "La consulta supera el límite de 6000 caracteres."
        provider_prompt = text
        key = str(session_key or "").strip()
        if key:
            self._prune_memory()
            state = self._memory.get(key)
            if state and state.get("turns"):
                context = state["turns"][-self.memory_turns * 2:]
                formatted = "\n".join(
                    f"{'USUARIO' if item['role'] == 'user' else 'STELLA'}: {item['content']}"
                    for item in context
                )
                provider_prompt = (
                    "CONTEXTO RECIENTE Y TEMPORAL (solo para dar continuidad; no lo trates como memoria permanente):\n"
                    + formatted + "\n\nCONSULTA ACTUAL:\n" + text
                )
        instructions = (system or STELLA_SYSTEM)[:3000]
        providers = (
            [("gemini", self._ask_gemini), ("groq", self._ask_groq)]
            if self.primary == "gemini"
            else [("groq", self._ask_groq), ("gemini", self._ask_gemini)]
        )
        errors = []
        for name, method in providers:
            if (name == "gemini" and not self.gemini_key) or (name == "groq" and not self.groq_key):
                continue
            try:
                answer = await method(provider_prompt, instructions)
                if key:
                    state = self._memory.setdefault(key, {"turns": [], "updated_at": time.monotonic()})
                    state["turns"].extend([
                        {"role": "user", "content": text[:2500]},
                        {"role": "assistant", "content": answer[:2500]},
                    ])
                    state["turns"] = state["turns"][-self.memory_turns * 2:]
                    state["updated_at"] = time.monotonic()
                    self._prune_memory()
                return answer
            except (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError) as exc:
                self.failures[name] += 1
                errors.append(name)
                log.warning("%s provider failed (%s); trying fallback if available", name, type(exc).__name__)
        if not errors:
            return "No hay proveedor de IA configurado. Añade Gemini o Groq en las variables privadas de Render."
        return "No pude obtener respuesta de Gemini ni de Groq. Revisa los modelos, las claves y los límites de ambas cuentas."

    async def translate(self, language: str, content: str) -> str:
        target = str(language or "").strip()[:60]
        text = str(content or "").strip()
        if not target or not text:
            return "Uso: !traducir <idioma> | <texto>"
        if len(text) > 4500:
            return "El texto supera el límite de 4500 caracteres."
        return await self.ask(
            f"Traduce el siguiente texto al idioma {target}. Conserva nombres propios, significado, tono y formato. Devuelve únicamente la traducción.\n\n{text}",
            system="Eres un traductor preciso. No añadas explicaciones, notas ni información que no figure en el original.",
        )

    async def optimize_music_query(self, description: str) -> str:
        result = await self.ask(
            "Convierte esta petición musical en una búsqueda corta en inglés para encontrar audio con licencia abierta. "
            "Conserva género, ambiente, tempo e instrumentos. Devuelve solo palabras clave, sin enlaces ni instrucciones.\n\n"
            + str(description or "")[:500],
            system="Eres un asistente especializado en descubrir música con licencias abiertas.",
        )
        clean = re.sub(r"https?://\S+|www\.\S+", "", result, flags=re.I)
        return " ".join(clean.replace('"', "").split())[:120]
