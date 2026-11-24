from __future__ import annotations

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

from stella.bot import StellaBot
from stella.config import Settings
from stella.platform import AnarchyClient
from stella.storage import Store

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


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.initialize()
    store.cleanup_expired()
    yield
    await platform.close()


app = FastAPI(title="Stella · Anarchy Community Bot", lifespan=lifespan)


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
    raw = await request.body()
    if len(raw) > MAX_WEBHOOK_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Webhook payload too large.")
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

    key = _event_key(event, raw)
    if key in seen_events:
        seen_events.move_to_end(key)
        return {"ok": True, "duplicate": True}
    seen_events[key] = time.monotonic()
    while len(seen_events) > SEEN_EVENTS_LIMIT:
        seen_events.popitem(last=False)

    try:
        await bot.handle_event(event)
    except Exception:
        log.exception("Event processing failed; event id=%s", key[:80])
        # Acknowledge authenticated events to avoid platform retry storms.
    return {"ok": True}


@app.get("/")
async def root() -> dict[str, str]:
    return {"name": "Stella", "status": "online", "docs": "/docs"}


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "bot": "Stella", "version": settings.version}


@app.post("/webhook")
async def webhook(request: Request) -> dict[str, bool]:
    return await receive_webhook(request)


@app.post("/")
async def webhook_root(request: Request) -> dict[str, bool]:
    return await receive_webhook(request)
