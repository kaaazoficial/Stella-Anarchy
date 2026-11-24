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
                """
            )

    def cleanup_expired(self, now: float | None = None) -> int:
        cutoff = (time.time() if now is None else now) - WARNING_RETENTION_DAYS * 86400
        with self._session(self.connect()) as db:
            cursor = db.execute(
                "DELETE FROM moderation_warnings WHERE created_at < ?", (cutoff,)
            )
            return max(0, cursor.rowcount)

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
