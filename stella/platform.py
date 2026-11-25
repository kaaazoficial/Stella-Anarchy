from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from typing import Any
from urllib.parse import quote

import httpx

log = logging.getLogger("stella.platform")


class AnarchyClient:
    """Small client for the documented Anarchy bot API."""

    def __init__(self, api_base: str, token: str) -> None:
        self.api_base = api_base.rstrip("/")
        self.token = token
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        # Anarchy limits message creation per bot. Serialize sends and keep
        # enough distance between requests so multi-page replies are delivered.
        self._message_send_lock = asyncio.Lock()
        self._last_message_send_at = 0.0
        self._message_min_interval = 0.5
        self._message_max_attempts = 4
        # IDs only; message text and conversation content are never retained.
        self._sent_message_ids: dict[str, deque[tuple[float, str]]] = defaultdict(deque)
        self._sent_message_ttl = 7 * 24 * 60 * 60
        self._sent_message_limit = 500
        self.identity: dict[str, Any] = {}

    async def close(self) -> None:
        await self._client.aclose()

    async def request(
        self, method: str, path: str, *, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self.token:
            log.error("ANARCHY_BOT_TOKEN is not configured.")
            return {"ok": False, "status": 503, "data": {}}
        try:
            response = await self._client.request(
                method,
                f"{self.api_base}{path}",
                headers={
                    "Authorization": f"Bot {self.token}",
                    "Accept": "application/json",
                },
                json=json_body,
            )
            try:
                data = response.json()
            except ValueError:
                data = {"message": response.text[:500]}
            return {
                "ok": response.is_success,
                "status": response.status_code,
                "data": data,
                "retry_after": response.headers.get("retry-after"),
            }
        except httpx.HTTPError as exc:
            log.warning("Anarchy API request failed: %s", type(exc).__name__)
            return {"ok": False, "status": 502, "data": {}}

    async def upload_bytes(
        self, path: str, content: bytes, content_type: str
    ) -> dict[str, Any]:
        """Upload bounded binary data to an Anarchy bot API endpoint."""
        if not self.token:
            return {"ok": False, "status": 503, "data": {}}
        if not isinstance(content, bytes) or not content or len(content) > 512 * 1024:
            return {"ok": False, "status": 413, "data": {"error": "Invalid upload size"}}
        try:
            response = await self._client.put(
                f"{self.api_base}{path}",
                headers={
                    "Authorization": f"Bot {self.token}",
                    "Accept": "application/json",
                    "Content-Type": str(content_type),
                },
                content=content,
            )
            try:
                data = response.json()
            except ValueError:
                data = {"message": response.text[:500]}
            return {"ok": response.is_success, "status": response.status_code, "data": data}
        except httpx.HTTPError as exc:
            log.warning("Anarchy binary upload failed: %s", type(exc).__name__)
            return {"ok": False, "status": 502, "data": {}}

    async def get_bot_identity(self) -> dict[str, Any] | None:
        """Return the authenticated bot identity from Anarchy GET /me."""
        result = await self.request("GET", "/me")
        if not result.get("ok"):
            log.warning("Anarchy identity lookup failed with status %s", result.get("status"))
            return None
        data = result.get("data")
        if not isinstance(data, dict):
            return None
        identity = data.get("bot") or data.get("profile") or data
        if not isinstance(identity, dict):
            return None
        self.identity = dict(identity)
        return self.identity

    @staticmethod
    def _extract_message_id(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        for key in ("messageId", "message_id", "id"):
            value = payload.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
        for key in ("message", "data", "result", "item"):
            found = AnarchyClient._extract_message_id(payload.get(key))
            if found:
                return found
        return None

    def _remember_sent_message(self, thread_id: str, message_id: str) -> None:
        now = time.time()
        history = self._sent_message_ids[str(thread_id)]
        cutoff = now - self._sent_message_ttl
        while history and history[0][0] < cutoff:
            history.popleft()
        if not any(existing_id == message_id for _, existing_id in history):
            history.append((now, message_id))
        while len(history) > self._sent_message_limit:
            history.popleft()
        if not history:
            self._sent_message_ids.pop(str(thread_id), None)

    def has_sent_message(self, thread_id: str, message_id: str) -> bool:
        """Check a referenced message against the bounded, content-free send ledger."""
        room_id = str(thread_id or "").strip()
        target_id = str(message_id or "").strip()
        history = self._sent_message_ids.get(room_id) if room_id and target_id else None
        if not history:
            return False
        cutoff = time.time() - self._sent_message_ttl
        while history and history[0][0] < cutoff:
            history.popleft()
        found = any(item_id == target_id for _, item_id in history)
        if not history:
            self._sent_message_ids.pop(room_id, None)
        return found

    async def delete_tracked_messages(self, thread_id: str) -> dict[str, int]:
        """Delete only recent IDs returned by this client's successful sends."""
        room_id = str(thread_id or "").strip()
        history = self._sent_message_ids.get(room_id)
        if not room_id or not history:
            return {"attempted": 0, "deleted": 0, "failed": 0}
        cutoff = time.time() - self._sent_message_ttl
        active = [(stamp, item_id) for stamp, item_id in history if stamp >= cutoff]
        deleted = failed = 0
        remaining: deque[tuple[float, str]] = deque()
        for stamp, message_id in active:
            result = await self.request(
                "DELETE",
                f"/threads/{quote(room_id, safe='')}/messages/{quote(message_id, safe='')}",
            )
            if result.get("ok") or result.get("status") == 404:
                deleted += 1
            else:
                failed += 1
                remaining.append((stamp, message_id))
        if remaining:
            self._sent_message_ids[room_id] = remaining
        else:
            self._sent_message_ids.pop(room_id, None)
        return {"attempted": len(active), "deleted": deleted, "failed": failed}

    @staticmethod
    def _retry_after_seconds(value: Any, fallback: float) -> float:
        try:
            delay = float(str(value).strip())
        except (TypeError, ValueError):
            delay = fallback
        # Avoid tight retry loops and unreasonable waits from malformed headers.
        return min(30.0, max(0.5, delay))

    async def send_message(
        self, thread_id: str, content: str, *, reply_id: str | None = None
    ) -> bool:
        """Send one message with per-client pacing and bounded HTTP 429 retries."""
        room_id = str(thread_id)
        body: dict[str, Any] = {"content": content[:1900]}
        if reply_id:
            body["replyMessageId"] = reply_id

        async with self._message_send_lock:
            for attempt in range(self._message_max_attempts):
                elapsed = time.monotonic() - self._last_message_send_at
                wait_for_slot = self._message_min_interval - elapsed
                if wait_for_slot > 0:
                    await asyncio.sleep(wait_for_slot)

                self._last_message_send_at = time.monotonic()
                result = await self.request(
                    "POST", f"/threads/{quote(room_id, safe='')}/messages",
                    json_body=body,
                )
                if result.get("ok"):
                    message_id = self._extract_message_id(result.get("data"))
                    if message_id:
                        self._remember_sent_message(room_id, message_id)
                    return True

                if int(result.get("status") or 0) != 429:
                    return False

                if attempt + 1 >= self._message_max_attempts:
                    log.error(
                        "Anarchy rejected a message after %s attempts (HTTP 429).",
                        self._message_max_attempts,
                    )
                    return False

                fallback = 0.5 * (2 ** attempt)
                retry_delay = self._retry_after_seconds(
                    result.get("retry_after"), fallback
                )
                log.warning(
                    "Anarchy message rate limit reached; retrying in %.2f seconds "
                    "(attempt %s/%s).",
                    retry_delay,
                    attempt + 1,
                    self._message_max_attempts,
                )
                await asyncio.sleep(retry_delay)

        return False
