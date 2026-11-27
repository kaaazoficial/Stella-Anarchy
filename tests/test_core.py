from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest

from main import verify_signature
from stella.config import Settings, _uid_set
from stella.storage import Store


def test_signature_verification_accepts_valid_payload():
    secret = "test-secret"
    raw = b'{"eventId":"evt-1"}'
    stamp = str(int(time.time()))
    digest = hmac.new(secret.encode(), stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    assert verify_signature(raw, stamp, f"v1={digest}", secret)


def test_signature_verification_rejects_tampering_and_old_timestamp():
    secret = "test-secret"
    raw = b'{"eventId":"evt-1"}'
    stamp = str(int(time.time()) - 600)
    digest = hmac.new(secret.encode(), stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    assert not verify_signature(raw + b" ", stamp, digest, secret)
    assert not verify_signature(raw, stamp, digest, secret)


def test_uid_parser_is_exact_and_deduplicated():
    assert _uid_set("one, two;three\none") == frozenset({"one", "two", "three"})


def test_warning_records_expire_after_ninety_days(tmp_path: Path):
    store = Store(tmp_path / "stella.sqlite3")
    store.initialize()
    old = time.time() - 91 * 86400
    with store._session(store.connect()) as db:
        db.execute(
            """INSERT INTO moderation_warnings
               (room_id,target_uid,target_name,moderator_uid,moderator_name,reason,created_at)
               VALUES('room','u1','User','admin','Admin','old',?)""",
            (old,),
        )
    assert store.cleanup_expired()["warnings"] == 1
    assert store.warnings("room") == []


def test_room_settings_are_isolated(tmp_path: Path):
    store = Store(tmp_path / "stella.sqlite3")
    store.initialize()
    store.set_setting("room-a", "antispam_enabled", "0")
    assert store.get_setting("room-a", "antispam_enabled", "1") == "0"
    assert store.get_setting("room-b", "antispam_enabled", "1") == "1"


def test_poll_vote_changes_and_closes(tmp_path: Path):
    store = Store(tmp_path / "stella.sqlite3")
    store.initialize()
    poll_id = store.create_poll("room", "creator", "Favourite?", ["A", "B"])
    assert store.vote_poll("room", poll_id, "voter", 0) == "ok"
    assert store.vote_poll("room", poll_id, "voter", 1) == "ok"
    poll = store.get_poll("room", poll_id)
    assert poll["results"] == {1: 1}
    assert store.close_poll("room", poll_id, "creator") == "ok"
    assert store.vote_poll("room", poll_id, "voter-2", 0) == "closed"


def test_event_rsvp_and_creator_controls(tmp_path: Path):
    store = Store(tmp_path / "stella.sqlite3")
    store.initialize()
    event_id = store.create_event("room", "owner", "Meeting", time.time() + 3600)
    assert store.rsvp_event("room", event_id, "guest", "Guest") == "ok"
    assert store.close_event("room", event_id, "other") == "denied"
    assert store.close_event("room", event_id, "owner") == "ok"


def test_activity_is_aggregated_per_day_and_room(tmp_path: Path):
    store = Store(tmp_path / "stella.sqlite3")
    store.initialize()
    store.record_activity("room-a", "uid-1")
    store.record_activity("room-a", "uid-1")
    store.record_activity("room-b", "uid-1")
    assert store.activity_summary("room-a")[0]["total_messages"] == 2
    assert store.activity_summary("room-b")[0]["total_messages"] == 1



def test_log_bot_identity_reports_bot_id_and_uid(caplog):
    import logging

    from main import log_bot_identity

    with caplog.at_level(logging.INFO, logger="stella"):
        log_bot_identity({
            "name": "Stella",
            "botId": "bot-id-test",
            "uid": "account-uid-test",
        })

    assert "botId=bot-id-test" in caplog.text
    assert "uid=account-uid-test" in caplog.text
    assert "token" not in caplog.text.casefold()


def test_log_bot_identity_warns_when_bot_id_is_missing(caplog):
    import logging

    from main import log_bot_identity

    with caplog.at_level(logging.WARNING, logger="stella"):
        log_bot_identity({"uid": "account-uid-test"})

    assert "returned no botId" in caplog.text



def test_webhook_event_key_deduplication_is_committed_only_after_success(monkeypatch):
    import asyncio
    import main
    from fastapi import HTTPException

    class Request:
        def __init__(self, body):
            self.body = body
            self.headers = {
                "X-Anarchy-Bot-Timestamp": str(int(time.time())),
                "X-Anarchy-Bot-Signature": "",
            }

        async def stream(self):
            yield self.body

    async def scenario():
        raw = json.dumps({"eventId": "retry-me", "type": "message", "message": "malformed"}).encode()
        stamp = Request(raw).headers["X-Anarchy-Bot-Timestamp"]
        signature = hmac.new(
            main.settings.webhook_secret.encode(),
            stamp.encode("ascii") + b"." + raw,
            hashlib.sha256,
        ).hexdigest()
        request = Request(raw)
        request.headers["X-Anarchy-Bot-Signature"] = signature
        monkeypatch.setattr(main.settings, "webhook_secret", "test-secret", raising=False)
        request.headers["X-Anarchy-Bot-Signature"] = hmac.new(
            b"test-secret", stamp.encode("ascii") + b"." + raw, hashlib.sha256
        ).hexdigest()

        calls = {"count": 0}
        async def fail_once(event):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("transient")
        monkeypatch.setattr(main.bot, "handle_event", fail_once)
        key = main._event_key(json.loads(raw), raw)
        main.seen_events.pop(key, None)
        main.event_locks.pop(key, None)
        with pytest.raises(HTTPException) as error:
            await main.receive_webhook(request)
        assert error.value.status_code == 503
        assert key not in main.seen_events
        result = await main.receive_webhook(request)
        assert result == {"ok": True}
        assert calls["count"] == 2
        assert key in main.seen_events

    asyncio.run(scenario())


def test_duplicate_continuar_route_does_not_override_ai_continuation():
    import inspect
    from stella.bot import StellaBot

    source = inspect.getsource(StellaBot._command)
    assert '"continuar": self._ai' in source
    assert '"reanudar": self._voice_command' in source
    assert '"reanudar": self._voice_command, "continuar": self._voice_command' not in source



def test_webhook_duplicate_delivery_is_processed_once_after_success(monkeypatch):
    import asyncio
    import main

    async def scenario():
        raw = json.dumps({"eventId": "duplicate-after-success", "type": "future.event"}).encode()
        stamp = str(int(time.time()))
        signature = hmac.new(
            b"test-secret", stamp.encode("ascii") + b"." + raw, hashlib.sha256
        ).hexdigest()

        class Request:
            headers = {
                "X-Anarchy-Bot-Timestamp": stamp,
                "X-Anarchy-Bot-Signature": signature,
            }
            async def stream(self):
                yield raw

        monkeypatch.setattr(main.settings, "webhook_secret", "test-secret", raising=False)
        calls = 0
        async def handle(event):
            nonlocal calls
            calls += 1
        monkeypatch.setattr(main.bot, "handle_event", handle)
        key = main._event_key(json.loads(raw), raw)
        main.seen_events.pop(key, None)
        main.event_locks.pop(key, None)

        assert await main.receive_webhook(Request()) == {"ok": True}
        assert await main.receive_webhook(Request()) == {"ok": True, "duplicate": True}
        assert calls == 1

    asyncio.run(scenario())
