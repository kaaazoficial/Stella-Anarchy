"""Opt-in, durable AI conversation and fact memory backed by Qdrant."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from typing import Any
from urllib.parse import quote

import httpx

log = logging.getLogger("stella.ai_memory")

_ALLOWED_FACT_TOPICS = frozenset({
    "nota", "proyecto", "objetivo", "preferencia", "estudio", "trabajo", "interes",
})
_MEMORY_SECRET_RE = re.compile(
    r"(?i)(?:api[_ -]?key|access[_ -]?token|token|password|contraseñ|clave|secret|"
    r"bearer|https?://|www\.|[\w.+-]+@[\w.-]+\.[a-z]{2,}|\b\d{7,}\b)"
)
_MEMORY_SENSITIVE_RE = re.compile(
    r"(?i)\b(?:mi diagn[oó]stico|mi enfermedad|mi trastorno|mi tratamiento|"
    r"me diagnosticaron|tengo (?:depresi[oó]n|ansiedad|bipolaridad|"
    r"diabetes|epilepsia|vih|c[aá]ncer|una enfermedad)|tomo (?:medicaci[oó]n|"
    r"antidepresivos|ansiol[ií]ticos)|mi historial m[eé]dico|mi salud mental|"
    r"mi orientaci[oó]n sexual|mi vida sexual|mi religi[oó]n|soy (?:cat[oó]lic[oa]|"
    r"evang[eé]lic[oa]|musulm[aá]n|jud[ií]o|ate[oa])|mi partido pol[ií]tico|"
    r"voto por|mi domicilio|mi direcci[oó]n|mi n[uú]mero de tel[eé]fono|"
    r"mi correo electr[oó]nico)\b"
)
_EXPLICIT_REMEMBER_RE = re.compile(
    r"^(?:stella[, ]*)?(?:recuerda(?:me)?|no olvides|ten en cuenta|ten presente|"
    r"guarda en tu memoria|acu[eé]rdate de)\s+(?:que\s+)?(?P<fact>.+)$",
    re.IGNORECASE,
)
_STABLE_FACT_PATTERNS = (
    ("proyecto", re.compile(
        r"\b(?:mi proyecto|estoy (?:creando|desarrollando|construyendo|trabajando en))\s+"
        r"(?:se llama\s+|es\s+|consiste en\s+)?(?P<fact>[^.!?\n]{5,300})",
        re.IGNORECASE,
    )),
    ("objetivo", re.compile(
        r"\b(?:mi objetivo|mi meta|mi prop[oó]sito)\s+(?:es|consiste en)\s+"
        r"(?P<fact>[^.!?\n]{5,300})",
        re.IGNORECASE,
    )),
    ("preferencia", re.compile(
        r"\b(?:prefiero|me gusta|me encanta|no me gusta|no soporto)\s+"
        r"(?P<fact>[^.!?\n]{5,240})",
        re.IGNORECASE,
    )),
    ("estudio", re.compile(
        r"\b(?:estudio|estoy estudiando|mi carrera es|me estoy formando en)\s+"
        r"(?P<fact>[^.!?\n]{3,240})",
        re.IGNORECASE,
    )),
    ("trabajo", re.compile(
        r"\b(?:trabajo en|trabajo como|mi trabajo es|me dedico a)\s+"
        r"(?P<fact>[^.!?\n]{3,240})",
        re.IGNORECASE,
    )),
)


class QdrantConversationMemory:
    """Stores recent turns and durable, user-confirmed facts per room/member."""

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self.url = os.getenv("QDRANT_URL", "").strip().rstrip("/")
        self.api_key = os.getenv("QDRANT_API_KEY", "").strip()
        self.collection = os.getenv("QDRANT_AI_MEMORY_COLLECTION", "stella_ai_memory").strip()
        self.retention_days = max(1, min(90, int(os.getenv("AI_MEMORY_RETENTION_DAYS", "30"))))
        self.cleanup_interval_seconds = max(
            300, min(86400, int(os.getenv("AI_MEMORY_CLEANUP_INTERVAL_SECONDS", "21600")))
        )
        self._last_cleanup = 0.0
        self.max_turns = 10
        self.max_facts = 20
        self.client = client
        self._owns_client = client is None
        self._ready = False
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.api_key and self.collection)

    @property
    def status(self) -> str:
        if not self.enabled:
            return "no configurada: faltan QDRANT_URL o QDRANT_API_KEY"
        return (
            f"Qdrant disponible; retención de {self.retention_days} días por usuario y sala; "
            f"hasta {self.max_facts} recuerdos duraderos"
        )

    def _headers(self) -> dict[str, str]:
        return {"api-key": self.api_key, "Content-Type": "application/json"}

    def _collection_url(self) -> str:
        return f"{self.url}/collections/{quote(self.collection, safe='')}"

    async def _http(self) -> httpx.AsyncClient:
        if not self.enabled:
            raise RuntimeError("Qdrant AI memory is not configured")
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(12, connect=4),
                follow_redirects=False,
            )
            self._owns_client = True
        return self.client

    @staticmethod
    def _identity(room_id: str, uid: str) -> tuple[str, str]:
        raw = f"{str(room_id)}\0{str(uid)}"
        digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"stella-ai-memory:{digest}"))
        return digest, point_id

    async def ensure_collection(self) -> None:
        if self._ready:
            return
        async with self._lock:
            if self._ready:
                return
            client = await self._http()
            response = await client.get(self._collection_url(), headers=self._headers())
            if response.status_code == 404:
                created = await client.put(
                    self._collection_url(),
                    headers=self._headers(),
                    json={"vectors": {"size": 1, "distance": "Dot"}},
                )
                if created.status_code not in {200, 201, 409}:
                    created.raise_for_status()
                response = await client.get(self._collection_url(), headers=self._headers())
            response.raise_for_status()
            vectors = (
                response.json().get("result", {})
                .get("config", {}).get("params", {}).get("vectors", {})
            )
            size = vectors.get("size") if isinstance(vectors, dict) else None
            if size is not None and int(size) != 1:
                raise RuntimeError("Qdrant AI memory collection has an incompatible vector size")
            self._ready = True

    async def _cleanup_expired_if_due(self) -> None:
        """Physically remove expired payloads periodically, not only hide them on read."""
        now = time.monotonic()
        if now - self._last_cleanup < self.cleanup_interval_seconds:
            return
        self._last_cleanup = now
        try:
            client = await self._http()
            response = await client.post(
                f"{self._collection_url()}/points/delete?wait=true",
                headers=self._headers(),
                json={
                    "filter": {
                        "must": [{
                            "key": "expires_at",
                            "range": {"lt": int(time.time())},
                        }]
                    }
                },
            )
            response.raise_for_status()
        except Exception as exc:
            log.warning("Expired AI memory cleanup failed (%s).", type(exc).__name__)

    @staticmethod
    def _redact_secrets(content: str) -> str:
        text = re.sub(
            r"(?i)\b(api[_ -]?key|access[_ -]?token|token|password|contraseña|clave|secret)\s*[:=]\s*([^\s,;]+)",
            r"\1=[REDACTED]",
            content,
        )
        text = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [REDACTED]", text)
        text = re.sub(
            r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
            "[EMAIL OMITIDO]",
            text,
        )
        text = re.sub(r"(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w)", "[TELÉFONO OMITIDO]", text)
        return text

    @classmethod
    def _clean_turns(cls, turns: list[dict[str, Any]], limit: int = 10) -> list[dict[str, str]]:
        cleaned: list[dict[str, str]] = []
        safe_limit = max(1, min(20, int(limit)))
        source = turns if isinstance(turns, list) else []
        for item in source[-safe_limit:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "")
            if role not in {"user", "assistant"}:
                continue
            content = " ".join(str(item.get("content") or "").split())
            if not content:
                continue
            content = cls._redact_secrets(content)
            maximum = 1400 if role == "user" else 1200
            cleaned.append({"role": role, "content": content[:maximum]})
        return cleaned[-safe_limit:]

    @classmethod
    def _clean_facts(cls, facts: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
        cleaned: list[dict[str, Any]] = []
        safe_limit = max(1, min(30, int(limit)))
        for item in (facts if isinstance(facts, list) else [])[-safe_limit * 2:]:
            if not isinstance(item, dict):
                continue
            content = " ".join(str(item.get("content") or "").split()).strip()
            topic = str(item.get("topic") or "nota").casefold()
            if topic not in _ALLOWED_FACT_TOPICS:
                topic = "nota"
            if not content or len(content) > 360:
                continue
            if _MEMORY_SECRET_RE.search(content) or _MEMORY_SENSITIVE_RE.search(content):
                continue
            identifier = str(item.get("id") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{6,40}", identifier):
                identifier = uuid.uuid4().hex[:12]
            try:
                updated_at = int(item.get("updated_at") or time.time())
            except (TypeError, ValueError, OverflowError):
                updated_at = int(time.time())
            cleaned.append({
                "id": identifier,
                "topic": topic,
                "content": cls._redact_secrets(content),
                "updated_at": updated_at,
            })
        # Keep the newest version of a repeated fact, preserving its stable ID.
        deduplicated: dict[str, dict[str, Any]] = {}
        for item in cleaned:
            key = re.sub(r"\W+", " ", item["content"].casefold()).strip()
            previous = deduplicated.get(key)
            if previous is None or item["updated_at"] >= previous["updated_at"]:
                deduplicated[key] = item
        return sorted(deduplicated.values(), key=lambda item: item["updated_at"])[-safe_limit:]

    @staticmethod
    def extract_explicit_facts(value: str) -> list[dict[str, str]]:
        """Extract only clear memory requests or stable self-descriptions."""
        text = " ".join(str(value or "").split()).strip()
        if not text or len(text) > 1200 or text.startswith(("!", "-", "/")):
            return []
        match = _EXPLICIT_REMEMBER_RE.match(text)
        candidates: list[tuple[str, str]] = []
        if match:
            candidates.append(("nota", match.group("fact").strip()))
        else:
            for topic, pattern in _STABLE_FACT_PATTERNS:
                found = pattern.search(text)
                if found:
                    fact = found.group("fact").strip(" ,;:-")
                    if fact:
                        candidates.append((topic, fact))
        result = []
        for topic, fact in candidates[:2]:
            fact = " ".join(fact.split()).strip()
            if not fact or len(fact) > 360:
                continue
            if _MEMORY_SECRET_RE.search(fact) or _MEMORY_SENSITIVE_RE.search(fact):
                continue
            result.append({"topic": topic, "content": fact})
        return result

    async def _read_payload(self, room_id: str, uid: str) -> dict[str, Any] | None:
        await self.ensure_collection()
        digest, point_id = self._identity(room_id, uid)
        client = await self._http()
        response = await client.get(
            f"{self._collection_url()}/points/{point_id}",
            headers=self._headers(),
            params={"with_payload": "true", "with_vector": "false"},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        result = response.json().get("result")
        payload = result.get("payload", {}) if isinstance(result, dict) else {}
        if payload.get("identity") != digest:
            return None
        if int(payload.get("expires_at", 0)) <= int(time.time()):
            await self.forget(room_id, uid)
            return None
        return payload

    async def _write_payload(
        self, room_id: str, uid: str, payload: dict[str, Any], *, enabled: bool = True
    ) -> bool:
        digest, point_id = self._identity(room_id, uid)
        now = int(time.time())
        safe_payload = dict(payload)
        safe_payload.update({
            "identity": digest,
            "enabled": bool(enabled),
            "updated_at": now,
            "expires_at": now + self.retention_days * 86400,
        })
        safe_payload["turns"] = json.dumps(
            self._clean_turns(self._parse_list(safe_payload.get("turns")), self.max_turns),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        safe_payload["facts"] = json.dumps(
            self._clean_facts(self._parse_list(safe_payload.get("facts")), self.max_facts),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        client = await self._http()
        response = await client.put(
            f"{self._collection_url()}/points?wait=true",
            headers=self._headers(),
            json={"points": [{
                "id": point_id,
                "vector": [0.0],
                "payload": safe_payload,
            }]},
        )
        response.raise_for_status()
        return True

    @staticmethod
    def _parse_list(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                return []
        return value if isinstance(value, list) else []

    async def load(self, room_id: str, uid: str) -> list[dict[str, str]]:
        if not self.enabled or not room_id or not uid:
            return []
        try:
            await self.ensure_collection()
            await self._cleanup_expired_if_due()
            payload = await self._read_payload(room_id, uid)
            if not payload or payload.get("enabled") is not True:
                return []
            return self._clean_turns(self._parse_list(payload.get("turns")), self.max_turns)
        except Exception as exc:
            log.warning("Persistent AI memory load failed (%s).", type(exc).__name__)
            return []

    async def load_facts(self, room_id: str, uid: str) -> list[dict[str, Any]]:
        if not self.enabled or not room_id or not uid:
            return []
        try:
            await self.ensure_collection()
            await self._cleanup_expired_if_due()
            payload = await self._read_payload(room_id, uid)
            if not payload or payload.get("enabled") is not True:
                return []
            return self._clean_facts(self._parse_list(payload.get("facts")), self.max_facts)
        except Exception as exc:
            log.warning("Persistent AI fact-memory load failed (%s).", type(exc).__name__)
            return []

    async def save(self, room_id: str, uid: str, turns: list[dict[str, Any]]) -> bool:
        if not self.enabled or not room_id or not uid:
            return False
        try:
            await self.ensure_collection()
            await self._cleanup_expired_if_due()
            payload = await self._read_payload(room_id, uid)
            if not payload or payload.get("enabled") is not True:
                return False
            payload["turns"] = json.dumps(
                self._clean_turns(turns, self.max_turns),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            return await self._write_payload(room_id, uid, payload, enabled=True)
        except Exception as exc:
            log.warning("Persistent AI memory save failed (%s).", type(exc).__name__)
            return False

    async def remember_fact(
        self, room_id: str, uid: str, content: str, *, topic: str = "nota"
    ) -> str | None:
        if not self.enabled or not room_id or not uid:
            return None
        cleaned = self._clean_facts([{
            "id": uuid.uuid4().hex[:12],
            "topic": topic,
            "content": content,
            "updated_at": int(time.time()),
        }], 1)
        if not cleaned:
            return None
        try:
            await self.ensure_collection()
            await self._cleanup_expired_if_due()
            payload = await self._read_payload(room_id, uid)
            if not payload or payload.get("enabled") is not True:
                return None
            facts = self._clean_facts(self._parse_list(payload.get("facts")), self.max_facts)
            new_fact = cleaned[0]
            normalized = re.sub(r"\W+", " ", new_fact["content"].casefold()).strip()
            for index, existing in enumerate(facts):
                existing_normalized = re.sub(r"\W+", " ", existing["content"].casefold()).strip()
                if existing["topic"] == new_fact["topic"] and existing_normalized == normalized:
                    new_fact["id"] = existing["id"]
                    facts[index] = new_fact
                    break
            else:
                facts.append(new_fact)
            payload["facts"] = json.dumps(
                self._clean_facts(facts, self.max_facts),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            await self._write_payload(room_id, uid, payload, enabled=True)
            return new_fact["id"]
        except Exception as exc:
            log.warning("Persistent AI fact-memory save failed (%s).", type(exc).__name__)
            return None

    async def remember_from_message(self, room_id: str, uid: str, value: str) -> list[str]:
        """Capture only explicit remember requests and clear, stable first-person facts."""
        saved: list[str] = []
        for fact in self.extract_explicit_facts(value):
            identifier = await self.remember_fact(
                room_id, uid, fact["content"], topic=fact["topic"]
            )
            if identifier:
                saved.append(identifier)
        return saved

    async def forget_fact(self, room_id: str, uid: str, fact_id: str) -> bool:
        if not self.enabled or not room_id or not uid:
            return False
        target = str(fact_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,40}", target):
            return False
        try:
            await self.ensure_collection()
            await self._cleanup_expired_if_due()
            payload = await self._read_payload(room_id, uid)
            if not payload or payload.get("enabled") is not True:
                return False
            facts = self._clean_facts(self._parse_list(payload.get("facts")), self.max_facts)
            remaining = [item for item in facts if item["id"] != target]
            if len(remaining) == len(facts):
                return False
            payload["facts"] = json.dumps(remaining, ensure_ascii=False, separators=(",", ":"))
            return await self._write_payload(room_id, uid, payload, enabled=True)
        except Exception as exc:
            log.warning("Persistent AI fact-memory deletion failed (%s).", type(exc).__name__)
            return False

    async def is_enabled(self, room_id: str, uid: str) -> bool:
        if not self.enabled or not room_id or not uid:
            return False
        try:
            await self.ensure_collection()
            await self._cleanup_expired_if_due()
            payload = await self._read_payload(room_id, uid)
            return bool(payload and payload.get("enabled") is True)
        except Exception as exc:
            log.warning("Persistent AI memory preference read failed (%s).", type(exc).__name__)
            return False

    async def set_enabled(self, room_id: str, uid: str, enabled: bool) -> bool:
        if not self.enabled or not room_id or not uid:
            return False
        try:
            await self.ensure_collection()
            await self._cleanup_expired_if_due()
            payload = await self._read_payload(room_id, uid) or {}
            if enabled:
                return await self._write_payload(room_id, uid, payload, enabled=True)
            # Keep a durable opt-out tombstone but erase conversation and fact content.
            payload["turns"] = "[]"
            payload["facts"] = "[]"
            return await self._write_payload(room_id, uid, payload, enabled=False)
        except Exception as exc:
            log.warning(
                "Persistent AI memory %s failed (%s).",
                "opt-in" if enabled else "opt-out",
                type(exc).__name__,
            )
            return False

    async def clear(self, room_id: str, uid: str) -> bool:
        """Erase recent conversation turns while preserving durable facts and opt-in."""
        if not self.enabled or not room_id or not uid:
            return False
        try:
            await self.ensure_collection()
            payload = await self._read_payload(room_id, uid)
            if not payload or payload.get("enabled") is not True:
                return False
            payload["turns"] = "[]"
            return await self._write_payload(room_id, uid, payload, enabled=True)
        except Exception as exc:
            log.warning("Persistent AI memory clear failed (%s).", type(exc).__name__)
            return False

    async def forget(self, room_id: str, uid: str) -> bool:
        if not self.enabled or not room_id or not uid:
            return False
        try:
            await self.ensure_collection()
            await self._cleanup_expired_if_due()
            _, point_id = self._identity(room_id, uid)
            client = await self._http()
            response = await client.post(
                f"{self._collection_url()}/points/delete?wait=true",
                headers=self._headers(),
                json={"points": [point_id]},
            )
            response.raise_for_status()
            return True
        except Exception as exc:
            log.warning("Persistent AI memory deletion failed (%s).", type(exc).__name__)
            return False

    async def close(self) -> None:
        if self._owns_client and self.client is not None and not self.client.is_closed:
            await self.client.aclose()
