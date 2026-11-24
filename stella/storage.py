from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

WARNING_RETENTION_DAYS = 90


class Store:
    """Persist only moderation/configuration data needed to operate the bot."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    @staticmethod
    def _session(connection: sqlite3.Connection):
        class Session:
            def __enter__(self):
                return connection.__enter__()
            def __exit__(self, exc_type, exc, traceback):
                try:
                    return connection.__exit__(exc_type, exc, traceback)
                finally:
                    connection.close()
        return Session()

    def initialize(self) -> None:
        with self._session(self.connect()) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS moderation_warnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id TEXT NOT NULL,
                    target_uid TEXT NOT NULL,
                    target_name TEXT NOT NULL,
                    moderator_uid TEXT NOT NULL,
                    moderator_name TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    removed_at REAL,
                    removed_by TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_warnings_room_target
                    ON moderation_warnings(room_id, target_uid, id DESC);
                CREATE INDEX IF NOT EXISTS idx_warnings_created
                    ON moderation_warnings(created_at);
                CREATE TABLE IF NOT EXISTS room_settings (
                    room_id TEXT NOT NULL,
                    setting_key TEXT NOT NULL,
                    setting_value TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(room_id, setting_key)
                );

                CREATE TABLE IF NOT EXISTS polls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id TEXT NOT NULL,
                    creator_uid TEXT NOT NULL,
                    question TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    closed INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_polls_room ON polls(room_id, id DESC);
                CREATE TABLE IF NOT EXISTS poll_votes (
                    poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
                    voter_uid TEXT NOT NULL,
                    option_index INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(poll_id, voter_uid)
                );
                CREATE TABLE IF NOT EXISTS community_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id TEXT NOT NULL,
                    creator_uid TEXT NOT NULL,
                    title TEXT NOT NULL,
                    starts_at REAL NOT NULL,
                    location TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    closed INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_events_room ON community_events(room_id, starts_at);
                CREATE TABLE IF NOT EXISTS event_rsvps (
                    event_id INTEGER NOT NULL REFERENCES community_events(id) ON DELETE CASCADE,
                    user_uid TEXT NOT NULL,
                    nickname TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(event_id, user_uid)
                );
                CREATE TABLE IF NOT EXISTS activity_daily (
                    room_id TEXT NOT NULL,
                    user_uid TEXT NOT NULL,
                    day_key TEXT NOT NULL,
                    message_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(room_id, user_uid, day_key)
                );
                CREATE TABLE IF NOT EXISTS auto_replies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id TEXT NOT NULL,
                    trigger_text TEXT NOT NULL,
                    response_text TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_auto_replies_room ON auto_replies(room_id, enabled);
                """
            )

    def cleanup_expired(self, now: float | None = None) -> dict[str, int]:
        stamp = time.time() if now is None else float(now)
        warning_cutoff = stamp - WARNING_RETENTION_DAYS * 86400
        daily_cutoff = stamp - 7 * 86400
        event_cutoff = stamp - 30 * 86400
        poll_cutoff = stamp - 30 * 86400
        with self._session(self.connect()) as db:
            counts = {}
            statements = (
                ("warnings", "DELETE FROM moderation_warnings WHERE created_at < ?", warning_cutoff),
                ("activity", "DELETE FROM activity_daily WHERE day_key < date(?, 'unixepoch')", daily_cutoff),
                ("events", "DELETE FROM community_events WHERE starts_at < ?", event_cutoff),
                ("polls", "DELETE FROM polls WHERE created_at < ?", poll_cutoff),
            )
            for label, sql, value in statements:
                cursor = db.execute(sql, (value,))
                counts[label] = max(0, cursor.rowcount)
            return counts

    def add_warning(
        self, room_id: str, target_uid: str, target_name: str,
        moderator_uid: str, moderator_name: str, reason: str,
    ) -> int:
        with self._session(self.connect()) as db:
            cursor = db.execute(
                """INSERT INTO moderation_warnings
                   (room_id,target_uid,target_name,moderator_uid,moderator_name,reason,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (
                    str(room_id), str(target_uid), str(target_name)[:80],
                    str(moderator_uid), str(moderator_name)[:80],
                    str(reason or "Sin motivo indicado")[:240], time.time(),
                ),
            )
            return int(cursor.lastrowid)

    def warnings(self, room_id: str, target_uid: str | None = None) -> list[dict[str, Any]]:
        query = (
            "SELECT id,target_name,moderator_name,reason,active,created_at "
            "FROM moderation_warnings WHERE room_id=?"
        )
        params: list[Any] = [str(room_id)]
        if target_uid:
            query += " AND target_uid=?"
            params.append(str(target_uid))
        query += " ORDER BY id DESC LIMIT 15"
        with self._session(self.connect()) as db:
            return [dict(row) for row in db.execute(query, params).fetchall()]

    def remove_warning(self, room_id: str, warning_id: int, moderator_uid: str) -> bool:
        with self._session(self.connect()) as db:
            cursor = db.execute(
                """UPDATE moderation_warnings
                   SET active=0, removed_at=?, removed_by=?
                   WHERE id=? AND room_id=? AND active=1""",
                (time.time(), str(moderator_uid), int(warning_id), str(room_id)),
            )
            return cursor.rowcount > 0

    def get_setting(self, room_id: str, key: str, default: str) -> str:
        with self._session(self.connect()) as db:
            row = db.execute(
                "SELECT setting_value FROM room_settings WHERE room_id=? AND setting_key=?",
                (str(room_id), str(key)),
            ).fetchone()
            return str(row["setting_value"]) if row else default

    def set_setting(self, room_id: str, key: str, value: str) -> None:
        with self._session(self.connect()) as db:
            db.execute(
                """INSERT INTO room_settings(room_id,setting_key,setting_value,updated_at)
                   VALUES(?,?,?,?) ON CONFLICT(room_id,setting_key) DO UPDATE SET
                   setting_value=excluded.setting_value,updated_at=excluded.updated_at""",
                (str(room_id), str(key), str(value), time.time()),
            )


    def create_poll(self, room_id: str, creator_uid: str, question: str, options: list[str]) -> int:
        import json
        if not 2 <= len(options) <= 8:
            raise ValueError("A poll must have between 2 and 8 options.")
        now = time.time()
        with self._session(self.connect()) as db:
            cursor = db.execute(
                "INSERT INTO polls(room_id,creator_uid,question,options_json,created_at) VALUES(?,?,?,?,?)",
                (str(room_id), str(creator_uid), str(question)[:240], json.dumps(options, ensure_ascii=False), now),
            )
            return int(cursor.lastrowid)

    def get_poll(self, room_id: str, poll_id: int) -> dict[str, Any] | None:
        import json
        with self._session(self.connect()) as db:
            row = db.execute(
                "SELECT * FROM polls WHERE id=? AND room_id=?",
                (int(poll_id), str(room_id)),
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            result["options"] = json.loads(result.pop("options_json"))
            votes = db.execute(
                "SELECT option_index,COUNT(*) AS total FROM poll_votes WHERE poll_id=? GROUP BY option_index",
                (int(poll_id),),
            ).fetchall()
            result["results"] = {int(item["option_index"]): int(item["total"]) for item in votes}
            result["total_votes"] = sum(result["results"].values())
            return result

    def list_polls(self, room_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._session(self.connect()) as db:
            rows = db.execute(
                "SELECT id,question,closed,created_at FROM polls WHERE room_id=? ORDER BY id DESC LIMIT ?",
                (str(room_id), max(1, min(20, int(limit)))),
            ).fetchall()
            return [dict(row) for row in rows]

    def vote_poll(self, room_id: str, poll_id: int, voter_uid: str, option_index: int) -> str:
        now = time.time()
        with self._session(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT options_json,closed FROM polls WHERE id=? AND room_id=?",
                (int(poll_id), str(room_id)),
            ).fetchone()
            if not row:
                return "missing"
            if row["closed"]:
                return "closed"
            import json
            options = json.loads(row["options_json"])
            if option_index < 0 or option_index >= len(options):
                return "invalid_option"
            db.execute(
                """INSERT INTO poll_votes(poll_id,voter_uid,option_index,created_at)
                   VALUES(?,?,?,?) ON CONFLICT(poll_id,voter_uid) DO UPDATE SET
                   option_index=excluded.option_index,created_at=excluded.created_at""",
                (int(poll_id), str(voter_uid), int(option_index), now),
            )
            return "ok"

    def close_poll(self, room_id: str, poll_id: int, requester_uid: str, is_admin: bool = False) -> str:
        with self._session(self.connect()) as db:
            row = db.execute(
                "SELECT creator_uid,closed FROM polls WHERE id=? AND room_id=?",
                (int(poll_id), str(room_id)),
            ).fetchone()
            if not row:
                return "missing"
            if row["closed"]:
                return "closed"
            if str(row["creator_uid"]) != str(requester_uid) and not is_admin:
                return "denied"
            db.execute("UPDATE polls SET closed=1 WHERE id=?", (int(poll_id),))
            return "ok"

    def create_event(
        self, room_id: str, creator_uid: str, title: str, starts_at: float,
        location: str = "", description: str = "",
    ) -> int:
        with self._session(self.connect()) as db:
            cursor = db.execute(
                """INSERT INTO community_events
                   (room_id,creator_uid,title,starts_at,location,description,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (str(room_id), str(creator_uid), str(title)[:120], float(starts_at),
                 str(location)[:160], str(description)[:400], time.time()),
            )
            return int(cursor.lastrowid)

    def list_events(self, room_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._session(self.connect()) as db:
            rows = db.execute(
                """SELECT e.id,e.title,e.starts_at,e.location,e.description,e.closed,
                          COUNT(r.user_uid) AS attendees
                   FROM community_events e LEFT JOIN event_rsvps r ON r.event_id=e.id
                   WHERE e.room_id=? AND e.closed=0
                   GROUP BY e.id ORDER BY e.starts_at LIMIT ?""",
                (str(room_id), max(1, min(20, int(limit)))),
            ).fetchall()
            return [dict(row) for row in rows]

    def rsvp_event(self, room_id: str, event_id: int, user_uid: str, nickname: str) -> str:
        with self._session(self.connect()) as db:
            row = db.execute(
                "SELECT id,closed FROM community_events WHERE id=? AND room_id=?",
                (int(event_id), str(room_id)),
            ).fetchone()
            if not row:
                return "missing"
            if row["closed"]:
                return "closed"
            db.execute(
                """INSERT INTO event_rsvps(event_id,user_uid,nickname,created_at)
                   VALUES(?,?,?,?) ON CONFLICT(event_id,user_uid) DO UPDATE SET
                   nickname=excluded.nickname,created_at=excluded.created_at""",
                (int(event_id), str(user_uid), str(nickname)[:80], time.time()),
            )
            return "ok"

    def close_event(self, room_id: str, event_id: int, requester_uid: str, is_admin: bool = False) -> str:
        with self._session(self.connect()) as db:
            row = db.execute(
                "SELECT creator_uid,closed FROM community_events WHERE id=? AND room_id=?",
                (int(event_id), str(room_id)),
            ).fetchone()
            if not row:
                return "missing"
            if row["closed"]:
                return "closed"
            if str(row["creator_uid"]) != str(requester_uid) and not is_admin:
                return "denied"
            db.execute("UPDATE community_events SET closed=1 WHERE id=?", (int(event_id),))
            return "ok"

    def record_activity(self, room_id: str, user_uid: str, now: float | None = None) -> None:
        from datetime import datetime, timezone
        stamp = time.time() if now is None else float(now)
        day = datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()
        with self._session(self.connect()) as db:
            db.execute(
                """INSERT INTO activity_daily(room_id,user_uid,day_key,message_count)
                   VALUES(?,?,?,1) ON CONFLICT(room_id,user_uid,day_key) DO UPDATE SET
                   message_count=activity_daily.message_count+1""",
                (str(room_id), str(user_uid), day),
            )

    def activity_summary(self, room_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._session(self.connect()) as db:
            rows = db.execute(
                """SELECT user_uid,SUM(message_count) AS total_messages
                   FROM activity_daily WHERE room_id=?
                   GROUP BY user_uid ORDER BY total_messages DESC LIMIT ?""",
                (str(room_id), max(1, min(20, int(limit)))),
            ).fetchall()
            return [dict(row) for row in rows]

    def add_auto_reply(self, room_id: str, trigger_text: str, response_text: str) -> int:
        with self._session(self.connect()) as db:
            cursor = db.execute(
                "INSERT INTO auto_replies(room_id,trigger_text,response_text,created_at) VALUES(?,?,?,?)",
                (str(room_id), str(trigger_text).strip()[:80], str(response_text).strip()[:500], time.time()),
            )
            return int(cursor.lastrowid)

    def auto_reply_rules(self, room_id: str) -> list[dict[str, Any]]:
        with self._session(self.connect()) as db:
            rows = db.execute(
                "SELECT id,trigger_text,response_text FROM auto_replies WHERE room_id=? AND enabled=1 ORDER BY id DESC LIMIT 30",
                (str(room_id),),
            ).fetchall()
            return [dict(row) for row in rows]

    def remove_auto_reply(self, room_id: str, rule_id: int) -> bool:
        with self._session(self.connect()) as db:
            cursor = db.execute(
                "DELETE FROM auto_replies WHERE room_id=? AND id=?",
                (str(room_id), int(rule_id)),
            )
            return cursor.rowcount > 0
