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
    with store.connect() as db:
        db.execute(
            """INSERT INTO moderation_warnings
               (room_id,target_uid,target_name,moderator_uid,moderator_name,reason,created_at)
               VALUES('room','u1','User','admin','Admin','old',?)""",
            (old,),
        )
    assert store.cleanup_expired() == 1
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
