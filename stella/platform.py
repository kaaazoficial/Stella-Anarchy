from __future__ import annotations

import logging
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

    async def get_bot_identity(self) -> dict[str, Any] | None:
        """Return the authenticated bot identity from Anarchy GET /me."""
        result = await self.request("GET", "/me")
        if not result.get("ok"):
            log.warning("Anarchy identity lookup failed with status %s", result.get("status"))
            return None
        data = result.get("data")
        if not isinstance(data, dict):
            return None
        identity = data.get("bot")
        if not isinstance(identity, dict):
            return None
        return identity

    async def send_message(
        self, thread_id: str, content: str, *, reply_id: str | None = None
    ) -> bool:
        body: dict[str, Any] = {"content": content[:1900]}
        if reply_id:
            body["replyMessageId"] = reply_id
        result = await self.request(
            "POST", f"/threads/{quote(str(thread_id), safe='')}/messages",
            json_body=body,
        )
        return bool(result.get("ok"))
