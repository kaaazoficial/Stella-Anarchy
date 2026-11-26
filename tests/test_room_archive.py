from __future__ import annotations

import asyncio
import httpx

from stella.room_archive import RoomArchive, _safe_text


class FakeQdrant:
    def __init__(self):
        self.is_closed = False
        self.points = {}
        self.calls = []

    async def get(self, url, *, headers=None):
        self.calls.append(("GET", url, None))
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            request=request,
            json={
                "result": {
                    "config": {"params": {"vectors": {"size": 1}}},
                    "payload_schema": {
                        "room_id": {"data_type": "keyword"},
                        "event_type": {"data_type": "keyword"},
                        "order_key": {"data_type": "integer"},
                    },
                }
            },
        )

    async def put(self, url, *, headers=None, json=None):
        self.calls.append(("PUT", url, json))
        if "/points?" in url:
            for point in json["points"]:
                self.points[point["id"]] = {
                    "id": point["id"],
                    "payload": point["payload"],
                }
        request = httpx.Request("PUT", url)
        return httpx.Response(200, request=request, json={"result": {"status": "completed"}})

    async def post(self, url, *, headers=None, json=None):
        self.calls.append(("POST", url, json))
        request = httpx.Request("POST", url)
        if url.endswith("/points/count"):
            room = json["filter"]["must"][0]["match"]["value"]
            count = sum(
                1 for point in self.points.values()
                if point["payload"]["room_id"] == room
            )
            return httpx.Response(200, request=request, json={"result": {"count": count}})
        if url.endswith("/points/scroll"):
            clauses = json["filter"]["must"]
            room = clauses[0]["match"]["value"]
            order_by = json["order_by"]
            descending = order_by.get("direction") == "desc"
            start_from = order_by.get("start_from")
            points = []
            for point in self.points.values():
                payload = point["payload"]
                if payload["room_id"] != room:
                    continue
                if start_from is not None:
                    order_key = payload["order_key"]
                    if descending and order_key > start_from:
                        continue
                    if not descending and order_key < start_from:
                        continue
                matched = True
                for clause in clauses[1:]:
                    key = clause["key"]
                    if "match" in clause:
                        match = clause["match"]
                        expected = match.get("value", match.get("text", ""))
                        actual = str(payload.get(key) or "")
                        if "text" in match and str(expected).casefold() not in actual.casefold():
                            matched = False
                        elif "value" in match and actual != str(expected):
                            matched = False
                    elif "range" in clause:
                        value = int(payload.get(key) or 0)
                        bounds = clause["range"]
                        if "gte" in bounds and value < bounds["gte"]:
                            matched = False
                        if "lte" in bounds and value > bounds["lte"]:
                            matched = False
                if matched:
                    points.append(point)
            points.sort(
                key=lambda point: point["payload"]["order_key"],
                reverse=descending,
            )
            return httpx.Response(
                200,
                request=request,
                json={"result": {"points": points[:json["limit"]], "next_page_offset": None}},
            )
        return httpx.Response(200, request=request, json={"result": {}})

    async def aclose(self):
        self.is_closed = True


def _archive(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "https://qdrant.example")
    monkeypatch.setenv("QDRANT_API_KEY", "test-qdrant-key")
    monkeypatch.setenv("QDRANT_ROOM_ARCHIVE_COLLECTION", "stella_room_archive")
    archive = RoomArchive()
    fake = FakeQdrant()
    archive.client = fake
    return archive, fake


def test_archive_persists_complete_event_and_snapshot_without_expiry(monkeypatch):
    async def scenario():
        archive, fake = _archive(monkeypatch)
        saved = await archive.record_event(
            "room-191",
            "chat.message.in",
            event_time="2026-11-26T18:00:00Z",
            source_event_id="event-001",
            actor_uid="member-uid",
            actor_name="Miembro",
            message_id="12345",
            reply_to_message_id="12340",
            content="Hola, token=private-value",
            metadata={
                "webhook_event": {
                    "type": "message",
                    "eventId": "event-001",
                    "message": {
                        "content": "Hola, token=private-value",
                        "attachments": [{"type": "image", "name": "dibujo.png"}],
                    },
                }
            },
        )

        assert saved is True
        assert len(fake.points) == 1
        payload = next(iter(fake.points.values()))["payload"]
        assert payload["room_id"] == "room-191"
        assert payload["event_type"] == "chat.message.in"
        assert payload["actor_uid"] == "member-uid"
        assert payload["actor_name"] == "Miembro"
        assert payload["message_id"] == "12345"
        assert payload["reply_to_message_id"] == "12340"
        assert payload["content"] == "Hola, token=[REDACTED]"
        assert payload["metadata"]["webhook_event"]["message"]["attachments"][0]["name"] == "dibujo.png"
        assert "private-value" not in repr(payload)
        assert "expires_at" not in payload
        assert archive.status.startswith("conectado")
        await archive.close()

    asyncio.run(scenario())


def test_incoming_webhook_echo_and_outgoing_send_share_one_message_record(monkeypatch):
    async def scenario():
        archive, fake = _archive(monkeypatch)
        when = "2026-11-26T18:00:00Z"
        first = await archive.record_event(
            "room-191", "chat.message.out", event_time=when,
            message_id="message-55", content="Respuesta de Stella",
        )
        second = await archive.record_event(
            "room-191", "chat.message.in", event_time=when,
            source_event_id="webhook-55", message_id="message-55",
            content="Respuesta de Stella",
        )

        assert first and second
        assert len(fake.points) == 1
        payload = next(iter(fake.points.values()))["payload"]
        assert payload["order_key"] > 0
        assert payload["content"] == "Respuesta de Stella"
        await archive.close()

    asyncio.run(scenario())


def test_archive_is_scoped_and_read_in_chronological_order(monkeypatch):
    async def scenario():
        archive, fake = _archive(monkeypatch)
        await archive.record_event(
            "room-1", "chat.message.in",
            event_time="2026-11-26T10:00:00Z",
            source_event_id="a", content="Primero",
        )
        await archive.record_event(
            "room-1", "chat.message.edited",
            event_time="2026-11-26T10:01:00Z",
            source_event_id="b", content="Primero editado",
        )
        await archive.record_event(
            "room-2", "chat.message.in",
            event_time="2026-11-26T09:00:00Z",
            source_event_id="c", content="Otra sala",
        )

        events = await archive.list_room_events("room-1", limit=10)
        assert [event["content"] for event in events] == ["Primero", "Primero editado"]
        assert [event["event_type"] for event in events] == [
            "chat.message.in", "chat.message.edited"
        ]
        assert await archive.count_room_events("room-1") == 2
        assert await archive.count_room_events("room-2") == 1
        scroll_call = next(call for call in fake.calls if call[0] == "POST" and call[1].endswith("/points/scroll"))
        assert scroll_call[2]["order_by"]["key"] == "order_key"
        assert scroll_call[2]["order_by"]["direction"] == "asc"
        await archive.close()

    asyncio.run(scenario())


def test_archive_rejects_missing_configuration_without_network(monkeypatch):
    async def scenario():
        monkeypatch.delenv("QDRANT_URL", raising=False)
        monkeypatch.delenv("QDRANT_API_KEY", raising=False)
        archive = RoomArchive()
        assert archive.enabled is False
        assert await archive.record_event("room", "message", content="test") is False
        assert archive.client is None

    asyncio.run(scenario())


def test_archive_redacts_credentials_but_keeps_ordinary_conversation():
    source = "Mi api_key=abc-secret y luego nos vemos en la sala."
    cleaned = _safe_text(source)
    assert "abc-secret" not in cleaned
    assert "[REDACTED]" in cleaned
    assert "luego nos vemos en la sala" in cleaned



def test_archive_search_filters_by_content_user_and_timestamp(monkeypatch):
    async def scenario():
        archive, fake = _archive(monkeypatch)
        await archive.record_event(
            "room-1", "chat.message.in",
            event_time="2026-11-26T10:00:00Z",
            actor_uid="uid-a", actor_name="Aurora",
            message_id="m-1", content="La comunidad necesita reglas claras",
        )
        await archive.record_event(
            "room-1", "chat.message.in",
            event_time="2026-11-26T11:00:00Z",
            actor_uid="uid-b", actor_name="Bruma",
            message_id="m-2", content="Hablemos de música",
        )
        start = 1795680000000
        end = 1795687200000
        found = await archive.search_room_events(
            "room-1", "reglas", actor_uid="uid-a",
            start_ms=start, end_ms=end, limit=10,
        )
        assert len(found) == 1
        assert found[0]["actor_name"] == "Aurora"
        request = next(
            call[2] for call in fake.calls
            if call[0] == "POST" and call[1].endswith("/points/scroll")
        )
        assert request["order_by"]["direction"] == "desc"
        assert {"key": "actor_uid", "match": {"value": "uid-a"}} in request["filter"]["must"]
        assert {"key": "content", "match": {"text": "reglas"}} in request["filter"]["must"]
        assert any(item.get("key") == "occurred_at_ms" for item in request["filter"]["must"])
        await archive.close()

    asyncio.run(scenario())


def test_archive_reverse_pagination_uses_stable_order_cursor(monkeypatch):
    async def scenario():
        archive, _fake = _archive(monkeypatch)
        for index in range(4):
            await archive.record_event(
                "room-1", "chat.message.in",
                event_time=f"2026-11-26T10:0{index}:00Z",
                source_event_id=f"event-{index}",
                message_id=f"message-{index}", content=f"Mensaje {index}",
            )
        first = await archive.list_room_events("room-1", reverse=True, limit=2)
        second = await archive.list_room_events(
            "room-1", reverse=True,
            before_order_key=int(first[-1]["order_key"]), limit=2,
        )
        assert [item["content"] for item in first] == ["Mensaje 3", "Mensaje 2"]
        assert [item["content"] for item in second] == ["Mensaje 1", "Mensaje 0"]
        await archive.close()

    asyncio.run(scenario())


def test_recent_room_context_and_integrity_report(monkeypatch):
    async def scenario():
        archive, _fake = _archive(monkeypatch)
        await archive.record_event(
            "room-1", "chat.message.in",
            event_time="2026-11-26T10:00:00Z",
            message_id="message-1", content="Hola",
        )
        await archive.record_event(
            "room-1", "room.join",
            event_time="2026-11-26T10:01:00Z",
            source_event_id="join-1", content=None,
        )
        await archive.record_event(
            "room-1", "chat.message.edited",
            event_time="2026-11-26T10:02:00Z",
            source_event_id="edit-1", message_id="message-1",
            content="Hola, actualización",
        )
        context = await archive.recent_room_context("room-1", limit=10)
        report = await archive.integrity_check("room-1", sample_limit=10)
        assert [item["content"] for item in context] == ["Hola, actualización", "Hola"]
        assert report["total"] == 2
        assert report["sampled"] == 2
        assert report["missing_message_ids"] == 0
        assert report["missing_timestamps"] == 0
        await archive.close()

    asyncio.run(scenario())
