from __future__ import annotations

import re
from urllib.parse import urlparse

URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>()]+")
SUSPICIOUS_TLDS = {".zip", ".mov", ".click", ".top", ".work", ".country", ".gq", ".tk"}


def inspect_links(content: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for raw in URL_RE.findall(str(content or ""))[:10]:
        candidate = raw.rstrip(".,;:!?)]}")
        parsed = urlparse(candidate if "://" in candidate else "https://" + candidate)
        host = (parsed.hostname or "").casefold()
        if not host:
            findings.append({"url": candidate[:240], "risk": "invalid", "reason": "No se pudo reconocer el dominio."})
            continue
        risk = "low"
        reason = "Dominio detectado; esto no garantiza que el enlace sea seguro."
        if host in {"localhost", "127.0.0.1"} or host.endswith(".local"):
            risk, reason = "high", "El enlace apunta a una dirección local."
        elif any(host.endswith(tld) for tld in SUSPICIOUS_TLDS):
            risk, reason = "medium", "El dominio utiliza una extensión que merece revisión adicional."
        elif parsed.username or parsed.password:
            risk, reason = "high", "El enlace incluye credenciales en la dirección."
        findings.append({"url": candidate[:240], "domain": host[:120], "risk": risk, "reason": reason})
    return findings


def sanitize_reason(value: str) -> str:
    text = " ".join(str(value or "").split())
    return text[:240] or "Sin motivo indicado"
