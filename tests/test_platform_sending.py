import asyncio

from stella.platform import AnarchyClient


def _client_with_responses(responses):
    client = object.__new__(AnarchyClient)
    client._message_send_lock = asyncio.Lock()
    client._last_message_send_at = 0.0
    client._message_min_interval = 1.0
    client._message_max_attempts = 4
    client._message_backoff_until = 0.0
    calls = []

    async def request(method, path, *, json_body=None):
        calls.append((method, path, json_body))
        return responses.pop(0)

    client.request = request
    client._remember_sent_message = lambda *args: None
    return client, calls


def test_send_message_retries_http_429_and_then_succeeds(monkeypatch):
    async def scenario():
        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("stella.platform.asyncio.sleep", fake_sleep)
        client, calls = _client_with_responses([
            {"ok": False, "status": 429, "retry_after": "2", "data": {}},
            {"ok": True, "status": 200, "data": {}},
        ])
        result = await client.send_message("room", "page 4")

        assert result is True
        assert len(calls) == 2
        assert any(delay >= 2 for delay in sleeps)
        assert calls[0][2] == {"content": "page 4"}

    asyncio.run(scenario())


def test_send_message_stops_after_bounded_rate_limit_retries(monkeypatch):
    async def scenario():
        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr("stella.platform.asyncio.sleep", fake_sleep)
        client, calls = _client_with_responses([
            {"ok": False, "status": 429, "retry_after": None, "data": {}}
            for _ in range(4)
        ])
        result = await client.send_message("room", "page 6")

        assert result is False
        assert len(calls) == 4
        assert len(sleeps) >= 3
        assert 2.0 in sleeps and 4.0 in sleeps and 8.0 in sleeps

    asyncio.run(scenario())



def test_send_message_includes_valid_anarchy_buttons_and_returns_message_id(monkeypatch):
    async def scenario():
        async def fake_sleep(_delay):
            return None

        monkeypatch.setattr("stella.platform.asyncio.sleep", fake_sleep)
        client, calls = _client_with_responses([
            {"ok": True, "status": 200, "data": {"message": {"messageId": "90211"}}},
        ])
        result = await client.send_message(
            "room", "Selecciona una pista",
            buttons=[{"id": "play_1", "label": "▶ 1. Instrumental"}],
            return_message_id=True,
        )
        assert result == "90211"
        assert calls[0][2]["buttons"] == [{"id": "play_1", "label": "▶ 1. Instrumental"}]

    asyncio.run(scenario())


def test_send_message_rejects_more_than_five_or_invalid_buttons():
    async def scenario():
        client, calls = _client_with_responses([])
        too_many = [{"id": f"p{i}", "label": f"Pista {i}"} for i in range(6)]
        invalid_id = [{"id": "bad id", "label": "Pista"}]
        assert await client.send_message("room", "menu", buttons=too_many) is False
        assert await client.send_message("room", "menu", buttons=invalid_id) is False
        assert calls == []

    asyncio.run(scenario())


def test_edit_message_removes_buttons_using_documented_patch_route():
    async def scenario():
        client = object.__new__(AnarchyClient)
        calls = []

        async def request(method, path, *, json_body=None):
            calls.append((method, path, json_body))
            return {"ok": True, "status": 200, "data": {}}

        client.request = request
        assert await client.edit_message("room", "90211", remove_buttons=True)
        assert calls == [(
            "PATCH",
            "/threads/room/messages/90211",
            {"removeButtons": True},
        )]

    asyncio.run(scenario())


def test_send_message_converts_escaped_newlines_before_posting(monkeypatch):
    async def scenario():
        async def fake_sleep(_delay):
            return None

        monkeypatch.setattr("stella.platform.asyncio.sleep", fake_sleep)
        client, calls = _client_with_responses([
            {"ok": True, "status": 200, "data": {}},
        ])

        result = await client.send_message(
            "room",
            r"Primera línea\\nSegunda línea\\r\\nTercera línea",
        )

        assert result is True
        assert calls[0][2]["content"] == "Primera línea\nSegunda línea\nTercera línea"
        assert r"\\n" not in calls[0][2]["content"]

    asyncio.run(scenario())



def test_retry_after_accepts_http_date_and_respects_long_server_wait():
    from datetime import datetime, timezone, timedelta
    from email.utils import format_datetime

    future = datetime.now(timezone.utc) + timedelta(seconds=75)
    value = format_datetime(future, usegmt=True)
    delay = AnarchyClient._retry_after_seconds(value, 2.0)
    assert 70 <= delay <= 76
    assert AnarchyClient._retry_after_seconds("240", 2.0) == 240
    assert AnarchyClient._retry_after_seconds("999", 2.0) == 300



def test_rate_limit_cooldown_is_shared_with_following_messages(monkeypatch):
    async def scenario():
        sleeps = []
        async def fake_sleep(delay):
            sleeps.append(delay)
        monkeypatch.setattr("stella.platform.asyncio.sleep", fake_sleep)
        client, calls = _client_with_responses([
            {"ok": False, "status": 429, "retry_after": "2", "data": {}}
            for _ in range(4)
        ])
        assert await client.send_message("room", "first") is False
        assert len(calls) == 4
        cooldown = client._message_backoff_until
        assert cooldown > 0
        # A second send must respect the server cooldown even after the first
        # send exhausted its retry budget.
        client.request = _request_success = async_success
        assert await client.send_message("room", "second") is True
        assert any(delay >= 1.9 for delay in sleeps)

    async def async_success(method, path, *, json_body=None):
        return {"ok": True, "status": 200, "data": {}}

    asyncio.run(scenario())



def test_send_message_uses_only_documented_anarchy_body_fields(monkeypatch):
    async def scenario():
        async def fake_sleep(_delay):
            return None

        monkeypatch.setattr("stella.platform.asyncio.sleep", fake_sleep)
        client, calls = _client_with_responses([
            {"ok": True, "status": 200, "data": {}},
        ])
        sent = await client.send_message(
            "room", "@Persona mira esto", reply_id="90210",
            buttons=[{"id": "ok", "label": "OK"}],
        )
        assert sent is True
        assert calls[0][2] == {
            "content": "@Persona mira esto",
            "replyMessageId": "90210",
            "buttons": [{"id": "ok", "label": "OK"}],
        }

    asyncio.run(scenario())



def test_get_message_uses_documented_anarchy_read_route(monkeypatch):
    async def scenario():
        client = AnarchyClient("https://api.anarchyapps.org/bot-api/v1", "test-token")
        calls = []

        async def fake_request(method, path, *, json_body=None):
            calls.append((method, path, json_body))
            return {
                "ok": True,
                "status": 200,
                "data": {
                    "message": {
                        "messageId": "90210",
                        "content": "hola",
                        "author": {"uid": "target-uid", "nickname": "Persona X"},
                        "own": False,
                    }
                },
            }

        client.request = fake_request
        message = await client.get_message("48213", "90210")
        assert message["author"]["uid"] == "target-uid"
        assert calls == [("GET", "/threads/48213/messages/90210", None)]
        await client.close()

    asyncio.run(scenario())


def test_get_message_rejects_non_numeric_message_ids_without_api_call():
    async def scenario():
        client = AnarchyClient("https://api.anarchyapps.org/bot-api/v1", "test-token")
        calls = []

        async def fake_request(*args, **kwargs):
            calls.append((args, kwargs))
            return {"ok": False, "status": 404, "data": {}}

        client.request = fake_request
        assert await client.get_message("48213", "../other") is None
        assert calls == []
        await client.close()

    asyncio.run(scenario())
