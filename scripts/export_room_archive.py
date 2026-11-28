"""Export one Anarchy room's full chronological archive as JSON Lines."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stella.room_archive import RoomArchive  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    if not os.getenv("QDRANT_URL", "").strip() or not os.getenv("QDRANT_API_KEY", "").strip():
        raise RuntimeError("Configura QDRANT_URL y QDRANT_API_KEY.")
    archive = RoomArchive()
    output = sys.stdout
    should_close = False
    if args.output:
        output = open(args.output, "w", encoding="utf-8")
        should_close = True
    cursor = 0
    exported = 0
    try:
        await archive.ensure_collection()
        while True:
            batch_size = min(500, args.limit - exported) if args.limit else 500
            if batch_size <= 0:
                break
            events = await archive.list_room_events(
                args.room_id,
                after_order_key=cursor,
                limit=batch_size,
            )
            if not events:
                break
            for event in events:
                output.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                exported += 1
                cursor = max(cursor, int(event.get("order_key") or 0))
                if args.limit and exported >= args.limit:
                    break
            output.flush()
            if args.limit and exported >= args.limit:
                break
            if len(events) < batch_size:
                break
            if cursor <= 0:
                raise RuntimeError("El cursor de orden del archivo no avanzó.")
    finally:
        if should_close:
            output.close()
        await archive.close()
    print(f"Exportados {exported} eventos de la sala {args.room_id}.", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--room-id", required=True, help="ID de la sala de Anarchy.")
    parser.add_argument("--output", help="Ruta de salida JSONL; por defecto stdout.")
    parser.add_argument("--limit", type=int, default=0, help="Máximo de eventos (0 = todos).")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit no puede ser negativo.")
    try:
        return asyncio.run(run(args))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
