import asyncio

from stella.platform import AnarchyClient


def _client_with_responses(responses):
    client = object.__new__(AnarchyClient)
    client._message_send_lock = asyncio.Lock()
    client._last_message_send_at = 0.0
    client._message_min_interval = 1.0
    client._message_max_attempts = 4
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
        assert all(delay >= 1 for delay in sleeps)

    asyncio.run(scenario())
