from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from stella.bot import StellaBot
from stella.boot import IMPORT_STARTED_AT
from stella.config import Settings, get_gemini_api_key
from stella.platform import AnarchyClient
from stella.storage import Store
from stella.metrics import render_prometheus_metrics
from stella.storage_manager import verify_sqlite_database

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("stella")

settings = Settings.from_env()
store = Store(settings.database_path)
platform = AnarchyClient(settings.api_base, settings.bot_token)
bot = StellaBot(settings, store, platform)

MAX_WEBHOOK_BODY_BYTES = 256 * 1024
SIGNATURE_TOLERANCE_SECONDS = 300
SEEN_EVENTS_LIMIT = 3000
seen_events: OrderedDict[str, float] = OrderedDict()
PROCESS_STARTED_AT = IMPORT_STARTED_AT
METRICS: dict[str, float] = {
    "webhook_events": 0,
    "webhook_dispatch_completed": 0,
    "webhook_dispatch_failures": 0,
    "text_messages_received": 0,
    "commands_received": 0,
    "ai_requests": 0,
    "messages_sent": 0,
    "api_requests": 0,
}
bot.metrics = METRICS


def log_bot_identity(identity: dict[str, Any] | None) -> None:
    """Log only the authenticated bot's public identifiers, never its token."""
    if not identity:
        log.warning("Could not resolve Stella's Anarchy identity during startup.")
        return
    bot_id = str(identity.get("botId") or identity.get("bot_id") or "").strip()
    account_uid = str(identity.get("uid") or "").strip()
    bot_name = str(identity.get("name") or identity.get("nickname") or "Stella").strip()
    bot_kind = str(identity.get("kind") or "unknown").strip()
    read_all_messages = identity.get("readAllMessages")
    if not bot_id:
        log.warning("Anarchy authenticated the token but returned no botId.")
        return
    log.info(
        "Anarchy identity verified at startup: name=%s kind=%s readAllMessages=%s botId=%s uid=%s",
        bot_name[:80],
        bot_kind,
        read_all_messages if read_all_messages is not None else "unknown",
        bot_id,
        account_uid or "unavailable",
    )


async def _memory_maintenance_loop() -> None:
    """Run retention cleanup independently of incoming chat traffic."""
    interval = max(60, min(3600, int(os.getenv("MEMORY_MAINTENANCE_INTERVAL_SECONDS", "900"))))
    while True:
        try:
            await bot.cleanup_memory()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Automatic memory maintenance failed (%s).", type(exc).__name__)
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.initialize()
    store.cleanup_expired()
    maintenance_task = asyncio.create_task(
        _memory_maintenance_loop(),
        name="stella-memory-maintenance",
    )
    try:
        # Resolve the bot ID before it joins any room; no chat command is required.
        identity = await platform.get_bot_identity()
        log_bot_identity(identity)
        yield
    finally:
        maintenance_task.cancel()
        try:
            await maintenance_task
        except asyncio.CancelledError:
            pass
        await bot.close()


docs_enabled = os.getenv("API_DOCS_ENABLED", "").strip().casefold() in {"1", "true", "yes", "on"}
app = FastAPI(
    title="Stella · Anarchy Community Bot",
    lifespan=lifespan,
    docs_url="/docs" if docs_enabled else None,
    redoc_url="/redoc" if docs_enabled else None,
    openapi_url="/openapi.json" if docs_enabled else None,
)


def verify_signature(raw: bytes, timestamp: str, signature: str, secret: str) -> bool:
    if not raw or not timestamp or not signature or not secret:
        return False
    try:
        stamp = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(time.time() - stamp) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    supplied = signature.strip()
    if supplied.startswith("v1="):
        supplied = supplied[3:]
    expected = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("ascii") + b"." + raw,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, supplied)


def _event_key(event: dict[str, Any], raw: bytes) -> str:
    value = event.get("eventId") or event.get("id")
    return str(value).strip() if value else hashlib.sha256(raw).hexdigest()


async def receive_webhook(request: Request) -> dict[str, bool]:
    if not settings.webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook secret is not configured.")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_WEBHOOK_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Webhook payload too large.")
        chunks.append(chunk)
    raw = b"".join(chunks)
    timestamp = request.headers.get("X-Anarchy-Bot-Timestamp", "")
    signature = request.headers.get("X-Anarchy-Bot-Signature", "")
    if not verify_signature(raw, timestamp, signature, settings.webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")
    try:
        event = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be an object.")

    now = time.monotonic()
    while seen_events and now - next(iter(seen_events.values())) > 900:
        seen_events.popitem(last=False)
    key = _event_key(event, raw)
    if key in seen_events:
        seen_events.move_to_end(key)
        return {"ok": True, "duplicate": True}
    seen_events[key] = now
    while len(seen_events) > SEEN_EVENTS_LIMIT:
        seen_events.popitem(last=False)

    METRICS["webhook_events"] += 1
    if str(event.get("type") or "").casefold() in {"message", "message.created", "message_create"}:
        METRICS["text_messages_received"] += 1
        msg = event.get("message") or {}
        content = str(msg.get("content") or "")
        if content.startswith(settings.command_prefix):
            METRICS["commands_received"] += 1
            if content.split(maxsplit=1)[0].casefold() in {settings.command_prefix + "ia", settings.command_prefix + "asistente"}:
                METRICS["ai_requests"] += 1
    try:
        await bot.handle_event(event)
        METRICS["webhook_dispatch_completed"] += 1
    except Exception:
        METRICS["webhook_dispatch_failures"] += 1
        log.exception("Event processing failed; event id=%s", key[:80])
        # Acknowledge authenticated events to avoid platform retry storms.
    return {"ok": True}


@app.get("/")
async def root() -> dict[str, str]:
    return {"name": "Stella", "status": "online"}


@app.head("/")
async def root_head() -> Response:
    """Allow HTTP monitors configured to use HEAD instead of GET."""
    return Response(status_code=200)


@app.head("/health")
async def health_head() -> Response:
    """HEAD-compatible liveness endpoint for HTTP monitoring services."""
    response = await health()
    return Response(status_code=response.status_code)


@app.get("/health")
async def health() -> JSONResponse:
    """Liveness and privacy-safe component diagnostics for Render and operators."""
    database = verify_sqlite_database(settings.database_path)
    database_ok = bool(database.get("ok"))
    vector_memory = getattr(bot, "vector_memory", None)
    vector_status = (
        str(getattr(vector_memory, "status", "no inicializada"))
        if vector_memory is not None else "no inicializada"
    )
    room_archive = getattr(bot, "room_archive", None)
    room_archive_status = (
        str(getattr(room_archive, "status", "no inicializado"))
        if room_archive is not None else "no inicializado"
    )
    ai_configured = bool(
        get_gemini_api_key()
        or os.getenv("GROQ_API_KEY", "").strip()
    )
    ai_core = getattr(bot, "ai_core", None)
    ai_diagnostics = {}
    diagnostics_func = getattr(ai_core, "provider_diagnostics", None)
    if callable(diagnostics_func):
        try:
            ai_diagnostics = diagnostics_func()
        except Exception as exc:
            log.warning("AI provider diagnostics failed (%s).", type(exc).__name__)
            ai_diagnostics = {"status": "diagnostics_unavailable"}
    payload = {
        "ok": database_ok,
        "status": "healthy" if database_ok else "unhealthy",
        "bot": "Stella",
        "version": settings.version,
        "uptime_seconds": max(0, int(time.monotonic() - PROCESS_STARTED_AT)),
        "checks": {
            "database": str(database.get("integrity", "unknown")),
            "anarchy_token_configured": bool(settings.bot_token),
            "webhook_secret_configured": bool(settings.webhook_secret),
            "ai_provider_configured": ai_configured,
            "ai_providers": ai_diagnostics,
            "vector_memory": vector_status,
            "room_archive": room_archive_status,
        },
    }
    # Render and other HTTP monitors must receive a failing status when the
    # database is unhealthy; a JSON field alone is not enough for health checks.
    return JSONResponse(status_code=200 if database_ok else 503, content=payload)


@app.get("/ready")
async def readiness():
    """Readiness check: required Anarchy credentials and SQLite must be available."""
    database = verify_sqlite_database(settings.database_path)
    missing: list[str] = []
    if not settings.bot_token:
        missing.append("ANARCHY_BOT_TOKEN")
    if not settings.webhook_secret:
        missing.append("ANARCHY_WEBHOOK_SECRET")
    if not database.get("ok"):
        missing.append("database")
    ready = not missing
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"ready": ready, "missing": missing},
    )


@app.get("/metrics")
async def metrics_endpoint(request: Request):
    expected = os.getenv("PROMETHEUS_METRICS_TOKEN", "").strip()
    if expected:
        supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(expected, supplied):
            raise HTTPException(status_code=401, detail="Unauthorized")
    body, content_type = render_prometheus_metrics(
        METRICS,
        uptime_seconds=time.monotonic() - PROCESS_STARTED_AT,
    )
    return Response(content=body, media_type=content_type)


@app.post("/webhook")
async def webhook(request: Request) -> dict[str, bool]:
    return await receive_webhook(request)


@app.post("/")
async def webhook_root(request: Request) -> dict[str, bool]:
    return await receive_webhook(request)
