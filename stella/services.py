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
    async def fetch_avatar_image(self, source_url: str) -> tuple[bytes, str, dict[str, Any]]:
        from stella.images import validate_avatar_image_bytes
        from stella.research import is_safe_public_url

        raw = str(source_url or "").strip()
        if not is_safe_public_url(raw):
            raise ValueError("Usa una URL HTTPS pública.")
        parsed = urlparse(raw)
        allowed_hosts = {"upload.wikimedia.org", "i.imgur.com", "images.unsplash.com", "images.pexels.com"}
        host = (parsed.hostname or "").casefold()
        if host not in allowed_hosts:
            raise ValueError("Por seguridad, solo acepto imágenes directas de Wikimedia Commons, Imgur, Unsplash o Pexels.")
        async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
            async with client.stream("GET", raw, headers={"User-Agent": "StellaCommunityBot/1.0", "Accept": "image/png,image/jpeg,image/webp,image/gif"}) as response:
                if response.status_code != 200:
                    raise ValueError(f"La imagen respondió HTTP {response.status_code}. No se siguieron redirecciones.")
                mime = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().casefold()
                if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                    raise ValueError("El enlace no devuelve PNG, JPG, WebP o GIF.")
                length = response.headers.get("content-length")
                if length and length.isdigit() and int(length) > 512 * 1024:
                    raise ValueError("La imagen supera el límite de 512 KB.")
                chunks = []
                received = 0
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > 512 * 1024:
                        raise ValueError("La imagen supera el límite de 512 KB.")
                    chunks.append(chunk)
        content = b"".join(chunks)
        details = validate_avatar_image_bytes(content, mime, max_bytes=512 * 1024)
        return content, mime, details

    async def multimedia(self, kind: str, query: str, limit: int = 5) -> str:
        search_text = " ".join(str(query or "").split())[:120]
        media_kind = str(kind or "").casefold().strip()
        if not search_text:
            return "Escribe qué contenido quieres buscar."
        if media_kind == "imagen":
            async with httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": "StellaCommunityBot/1.0"}) as client:
                response = await client.get(
                    "https://commons.wikimedia.org/w/api.php",
                    params={
                        "action": "query", "generator": "search", "gsrsearch": search_text,
                        "gsrnamespace": 6, "gsrlimit": max(1, min(5, int(limit))),
                        "prop": "imageinfo", "iiprop": "url|extmetadata",
                        "iiextmetadatafilter": "LicenseShortName|Artist",
                        "format": "json", "formatversion": 2,
                    },
                )
                response.raise_for_status()
                pages = ((response.json().get("query") or {}).get("pages") or [])
            if not pages:
                return f"No encontré imágenes para «{search_text}» en Wikimedia Commons."
            lines = [f"IMÁGENES · WIKIMEDIA COMMONS · {search_text}"]
            for index, page in enumerate(pages[:max(1, min(5, int(limit)))], 1):
                info = (page.get("imageinfo") or [{}])[0]
                metadata = info.get("extmetadata") or {}
                license_name = re.sub(r"<[^>]+>", "", str((metadata.get("LicenseShortName") or {}).get("value") or "Licencia no indicada"))
                artist = re.sub(r"<[^>]+>", "", str((metadata.get("Artist") or {}).get("value") or ""))
                lines.append(f"{index}. {page.get('title') or 'Archivo'}\n{info.get('descriptionurl') or info.get('url') or ''}\nLicencia: {html.unescape(license_name)[:90]}" + (f" · Autor: {html.unescape(artist)[:90]}" if artist else ""))
            lines.append("Comprueba la licencia y la atribución en cada ficha antes de reutilizar una imagen.")
            return "\n\n".join(lines)
        archive_type = {"video": "movies", "audio": "audio"}.get(media_kind)
        if not archive_type:
            return "Tipos disponibles: imagen, video o audio."
        terms = re.findall(r"[\wÀ-ÿ]+", search_text, flags=re.UNICODE)[:6]
        if not terms:
            return "No encontré términos válidos para esa búsqueda."
        archive_query = f"mediatype:{archive_type} AND " + " AND ".join(terms)
        async with httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": "StellaCommunityBot/1.0"}) as client:
            response = await client.get(
                "https://archive.org/advancedsearch.php",
                params={"q": archive_query, "fl[]": ["identifier", "title", "creator", "year"], "rows": max(1, min(5, int(limit))), "page": 1, "output": "json"},
            )
            response.raise_for_status()
            docs = ((response.json().get("response") or {}).get("docs") or [])
        if not docs:
            return f"No encontré resultados de {media_kind} para «{search_text}» en Internet Archive."
        lines = [f"{media_kind.upper()} · INTERNET ARCHIVE · {search_text}"]
        for index, item in enumerate(docs[:max(1, min(5, int(limit)))], 1):
            identifier = str(item.get("identifier") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", identifier):
                continue
            title = html.unescape(str(item.get("title") or identifier))[:150]
            creator = html.unescape(str(item.get("creator") or ""))[:80]
            year = str(item.get("year") or "")[:12]
            details = " · ".join(value for value in (creator, year) if value)
            lines.append(f"{index}. {title}" + (f"\n{details}" if details else "") + f"\nhttps://archive.org/details/{quote_plus(identifier)}")
        if media_kind == "audio":
            lines.append("Son páginas de origen, no una autorización de uso. Revisa derechos y licencia; para reproducir audio usa !dj buscar.")
        return "\n\n".join(lines)


