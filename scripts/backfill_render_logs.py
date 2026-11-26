"""Backfill Stella's room archive from retained Render application audit logs.

Required environment: RENDER_API_KEY, RENDER_WORKSPACE_ID, RENDER_SERVICE_ID,
QDRANT_URL and QDRANT_API_KEY. Render log API queries are limited to its
available retention window; use --since and --until in ISO-8601 UTC.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stella.room_archive import RoomArchive  # noqa: E402

AUDIT_MARKER = "stella.audit:"


def parse_audit_log(log: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a structured Stella audit event from a Render app log line."""
    message = str(log.get("message") or "")
    position = message.find(AUDIT_MARKER)
    if position < 0:
        return None
    raw = message[position + len(AUDIT_MARKER):].strip()
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        # Some log formatters add a prefix/suffix; attempt the first complete object.
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            payload = json.loads(raw[start:end + 1])
        except (TypeError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None
    room_id = str(payload.get("room_id") or payload.get("thread_id") or "").strip()
    event_type = str(payload.get("event") or payload.get("event_type") or "").strip()
    if not room_id or not event_type:
        return None
    return {
        "room_id": room_id,
        "event_type": event_type,
        "event_time": payload.get("timestamp") or log.get("timestamp"),
        "source_event_id": payload.get("event_id") or payload.get("eventId"),
        "actor_uid": payload.get("uid") or payload.get("actor_uid"),
        "actor_name": payload.get("nickname") or payload.get("actor_name"),
        "message_id": payload.get("message_id") or payload.get("messageId"),
        "reply_to_message_id": (
            payload.get("reply_message_id")
            or payload.get("replyMessageId")
            or payload.get("reply_to_message_id")
        ),
        "content": payload.get("content") or payload.get("text"),
        "metadata": {
            "backfill_source": "render.stella.audit",
            "render_log_id": str(log.get("id") or ""),
            "audit_record": payload,
        },
    }


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def fetch_render_page(
    client: httpx.AsyncClient,
    *,
    owner_id: str,
    service_id: str,
    start_time: str,
    end_time: str,
) -> dict[str, Any]:
    params = [
        ("ownerId", owner_id),
        ("resource", service_id),
        ("startTime", start_time),
        ("endTime", end_time),
        ("direction", "forward"),
        ("limit", "100"),
        ("text", "stella.audit"),
    ]
    response = await client.get("https://api.render.com/v1/logs", params=params)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Render devolvió una respuesta de logs inesperada.")
    return payload


async def run(args: argparse.Namespace) -> int:
    render_key = os.getenv("RENDER_API_KEY", "").strip()
    owner_id = os.getenv("RENDER_WORKSPACE_ID", "").strip()
    service_id = os.getenv("RENDER_SERVICE_ID", "").strip()
    if not all((render_key, owner_id, service_id)):
        raise RuntimeError(
            "Faltan RENDER_API_KEY, RENDER_WORKSPACE_ID o RENDER_SERVICE_ID."
        )
    if not args.dry_run and not (
        os.getenv("QDRANT_URL", "").strip()
        and os.getenv("QDRANT_API_KEY", "").strip()
    ):
        raise RuntimeError("Faltan QDRANT_URL o QDRANT_API_KEY.")

    now = datetime.now(timezone.utc)
    start = parse_datetime(args.since)
    end = parse_datetime(args.until) if args.until else now
    if start >= end:
        raise ValueError("--since debe ser anterior a --until.")
    if end > now + timedelta(minutes=5):
        raise ValueError("--until no puede estar en el futuro.")
    if now - start > timedelta(days=30):
        raise ValueError(
            "La API de logs de Render admite consultas de hasta 30 días. "
            "Reduce --since al periodo que siga retenido."
        )

    archive = RoomArchive() if not args.dry_run else None
    if archive is not None:
        await archive.ensure_collection()

    headers = {
        "Authorization": f"Bearer {render_key}",
        "Accept": "application/json",
    }
    timeout = httpx.Timeout(45.0, connect=10.0)
    counts = {
        "pages": 0, "logs": 0, "parsed": 0, "imported": 0,
        "skipped": 0, "failed": 0,
    }
    cursor_start = start.isoformat().replace("+00:00", "Z")
    cursor_end = end.isoformat().replace("+00:00", "Z")
    semaphore = asyncio.Semaphore(8)

    async def import_event(event: dict[str, Any]) -> bool:
        assert archive is not None
        async with semaphore:
            return await archive.record_event(
                event["room_id"],
                event["event_type"],
                event_time=event["event_time"],
                source_event_id=event["source_event_id"],
                actor_uid=str(event["actor_uid"]) if event["actor_uid"] is not None else None,
                actor_name=str(event["actor_name"]) if event["actor_name"] is not None else None,
                message_id=str(event["message_id"]) if event["message_id"] is not None else None,
                reply_to_message_id=(
                    str(event["reply_to_message_id"])
                    if event["reply_to_message_id"] is not None else None
                ),
                content=str(event["content"]) if event["content"] is not None else None,
                metadata=event["metadata"],
            )

    try:
        async with httpx.AsyncClient(
            headers=headers, timeout=timeout, follow_redirects=False
        ) as client:
            while True:
                page = await fetch_render_page(
                    client,
                    owner_id=owner_id,
                    service_id=service_id,
                    start_time=cursor_start,
                    end_time=cursor_end,
                )
                counts["pages"] += 1
                logs = page.get("logs") or []
                if not isinstance(logs, list):
                    raise RuntimeError("La página de Render no contiene una lista de logs.")
                counts["logs"] += len(logs)
                events = []
                for item in logs:
                    if not isinstance(item, dict):
                        counts["skipped"] += 1
                        continue
                    event = parse_audit_log(item)
                    if event is None:
                        counts["skipped"] += 1
                        continue
                    events.append(event)
                counts["parsed"] += len(events)

                if args.dry_run:
                    pass
                elif events:
                    results = await asyncio.gather(
                        *(import_event(event) for event in events),
                        return_exceptions=True,
                    )
                    for result in results:
                        if isinstance(result, Exception):
                            counts["failed"] += 1
                        elif result:
                            counts["imported"] += 1
                        else:
                            counts["failed"] += 1

                print(
                    f"Página {counts['pages']}: logs={len(logs)} "
                    f"eventos={len(events)} importados={counts['imported']} "
                    f"fallos={counts['failed']}",
                    flush=True,
                )
                if not page.get("hasMore"):
                    break
                next_start = str(page.get("nextStartTime") or "").strip()
                next_end = str(page.get("nextEndTime") or "").strip()
                if not next_start or not next_end or (
                    next_start == cursor_start and next_end == cursor_end
                ):
                    raise RuntimeError("Render no devolvió un cursor de paginación válido.")
                cursor_start, cursor_end = next_start, next_end
    finally:
        if archive is not None:
            await archive.close()

    print(
        "Resumen: "
        + json.dumps(counts, ensure_ascii=False, sort_keys=True)
        + (" (simulación, no se escribió en Qdrant)." if args.dry_run else ""),
        flush=True,
    )
    return 1 if counts["failed"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since", required=True,
        help="Fecha inicial ISO-8601, por ejemplo 2026-11-24T22:20:36Z.",
    )
    parser.add_argument("--until", help="Fecha final ISO-8601 (por defecto: ahora UTC).")
    parser.add_argument("--dry-run", action="store_true", help="Cuenta eventos sin escribir en Qdrant.")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
