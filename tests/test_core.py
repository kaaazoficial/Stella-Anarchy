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
