from __future__ import annotations

import html
import logging
import os
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx

log = logging.getLogger("stella.services")


class _SearchParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._capture_title = False
        self._capture_snippet = False
        self._current: dict[str, str] | None = None
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "a" and "result__a" in classes:
            self._current = {"title": "", "url": values.get("href") or "", "snippet": ""}
            self._capture_title = True
        elif self._current is not None and tag in {"a", "div"} and "result__snippet" in classes:
            self._capture_snippet = True
            self._depth = 1
        elif self._capture_snippet and tag in {"div", "span", "b", "a"}:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._capture_title and tag == "a":
            self._capture_title = False
        if self._capture_snippet and tag in {"div", "span", "b", "a"}:
            self._depth -= 1
            if self._depth <= 0:
                self._capture_snippet = False
        if self._current and not self._capture_title and not self._capture_snippet:
            if self._current["title"].strip():
                self.results.append(self._current)
                self._current = None

    def handle_data(self, data: str) -> None:
        if self._current is None:
            return
        clean = " ".join(data.split())
        if self._capture_title:
            self._current["title"] += clean
        elif self._capture_snippet:
            self._current["snippet"] += clean


class StellaServices:
    """Optional external services. No prompts or responses are persisted."""

    def __init__(self) -> None:
        self.ai_key = os.getenv("AI_API_KEY", "").strip()
        self.ai_base = os.getenv(
            "AI_BASE_URL", "https://api.groq.com/openai/v1"
        ).rstrip("/")
        self.ai_model = os.getenv("AI_MODEL", "llama-3.3-70b-versatile").strip()
        self.timeout = httpx.Timeout(30.0, connect=8.0)

    async def close(self) -> None:
        return None

    async def ask(self, prompt: str, *, system: str | None = None) -> str:
        if not self.ai_key:
            return "La IA todavía no está configurada. Añade AI_API_KEY, AI_BASE_URL y AI_MODEL en las variables privadas del servicio."
        text = str(prompt or "").strip()
        if not text:
            return "Escribe una consulta después del comando."
        if len(text) > 6000:
            return "La consulta supera el límite de 6000 caracteres."
        messages = []
        if system:
            messages.append({"role": "system", "content": system[:1200]})
        messages.append({"role": "user", "content": text})
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.ai_base}/chat/completions",
                    headers={"Authorization": f"Bearer {self.ai_key}"},
                    json={
                        "model": self.ai_model,
                        "messages": messages,
                        "temperature": 0.6,
                        "max_tokens": 900,
                    },
                )
            if not response.is_success:
                log.warning("AI provider returned HTTP %s", response.status_code)
                return "El proveedor de IA no pudo completar la consulta. Revisa la clave, el modelo y los límites de tu cuenta."
            data = response.json()
            answer = data["choices"][0]["message"]["content"]
            answer = re.sub(r"(?is)<think>.*?</think>", "", str(answer)).strip()
            return answer[:7000] or "El proveedor no devolvió una respuesta de texto."
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            log.exception("AI request failed")
            return "No pude conectar con el proveedor de IA en este momento."

    async def translate(self, language: str, text: str) -> str:
        target = str(language or "").strip()
        source = str(text or "").strip()
        if not target or not source:
            return "Uso: !traducir <idioma> | <texto>"
        if len(source) > 4500:
            return "El texto supera el límite de 4500 caracteres."
        return await self.ask(
            f"Traduce el siguiente texto al idioma {target}. Conserva el sentido, el tono y el formato. Devuelve únicamente la traducción.\n\n{source}",
            system="Eres un traductor cuidadoso. No añadas explicaciones ni contenido que no esté en el original.",
        )

    async def research(self, query: str) -> list[dict[str, str]]:
        term = str(query or "").strip()
        if not 3 <= len(term) <= 160:
            return []
        try:
            from stella.research import search_web

            async with httpx.AsyncClient(
                timeout=self.timeout,
                headers={"User-Agent": "StellaCommunityBot/1.0 (+https://github.com/kaaazoficial/Stella-Anarchy)"},
                follow_redirects=False,
            ) as client:
                async def request_func(method: str, url: str, **kwargs):
                    kwargs.setdefault("timeout", self.timeout)
                    return await client.request(method, url, **kwargs)

                sources = await search_web(term, request_func, limit=8)
            return [
                {
                    "title": str(item.get("title") or "Fuente pública")[:180],
                    "url": str(item.get("url") or "")[:700],
                    "snippet": str(item.get("excerpt") or item.get("snippet") or "")[:700],
                    "domain": str(item.get("domain") or urlparse(str(item.get("url") or "")).netloc)[:120],
                    "source_type": str(item.get("source_type") or "web"),
                    "attribution": str(item.get("attribution") or "")[:240],
                }
                for item in sources[:8]
                if item.get("url")
            ]
        except (httpx.HTTPError, ValueError, RuntimeError, ImportError):
            log.exception("Research request failed")
            return []
