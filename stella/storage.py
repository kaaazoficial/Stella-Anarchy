from __future__ import annotations

import sqlite3
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

WARNING_RETENTION_DAYS = 90
MAX_SQLITE_INTEGER = 9223372036854775807

if hasattr(sys, "set_int_max_str_digits"):
    sys.set_int_max_str_digits(0)


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
                CREATE TABLE IF NOT EXISTS users (
                    uid TEXT PRIMARY KEY,
                    nickname TEXT NOT NULL,
                    bio TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    points INTEGER NOT NULL DEFAULT 0,
                    points_text TEXT NOT NULL DEFAULT '0',
                    created_at REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    profile_updated_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen);
                CREATE INDEX IF NOT EXISTS idx_users_points ON users(points DESC);
                CREATE TABLE IF NOT EXISTS affinities (
                    uid TEXT NOT NULL,
                    target_uid TEXT NOT NULL,
                    score INTEGER NOT NULL DEFAULT 0,
                    last_at REAL NOT NULL,
                    PRIMARY KEY(uid,target_uid)
                );
                CREATE TABLE IF NOT EXISTS gifts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender_uid TEXT NOT NULL,
                    sender_name TEXT NOT NULL,
                    target_uid TEXT NOT NULL,
                    target_name TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_gifts_target ON gifts(target_uid,id DESC);
                CREATE TABLE IF NOT EXISTS reward_claims (
                    uid TEXT NOT NULL,
                    action TEXT NOT NULL,
                    claimed_at REAL NOT NULL,
                    PRIMARY KEY(uid,action)
                );
                CREATE TABLE IF NOT EXISTS dj_playlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id TEXT NOT NULL,
                    owner_uid TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(room_id,owner_uid,name)
                );
                CREATE TABLE IF NOT EXISTS dj_playlist_tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    playlist_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    creator TEXT NOT NULL,
                    license TEXT NOT NULL,
                    source TEXT NOT NULL,
                    url TEXT NOT NULL,
                    item_url TEXT NOT NULL,
                    identifier TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(playlist_id) REFERENCES dj_playlists(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_dj_playlists_owner ON dj_playlists(room_id,owner_uid);
                CREATE INDEX IF NOT EXISTS idx_dj_tracks_playlist ON dj_playlist_tracks(playlist_id,id);
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
                CREATE TABLE IF NOT EXISTS point_wagers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id TEXT NOT NULL,
                    challenger_uid TEXT NOT NULL,
                    challenger_name TEXT NOT NULL,
                    target_uid TEXT NOT NULL,
                    target_name TEXT NOT NULL,
                    stake INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    challenger_roll INTEGER,
                    target_roll INTEGER,
                    winner_uid TEXT,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    settled_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_point_wagers_pending ON point_wagers(room_id,status,expires_at);
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
            if "points_text" not in columns:
                db.execute("ALTER TABLE users ADD COLUMN points_text TEXT NOT NULL DEFAULT '0'")
                db.execute("UPDATE users SET points_text=CAST(points AS TEXT)")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_points_text ON users(length(points_text) DESC, points_text DESC)"
            )

    @staticmethod
    def _read_points(row: sqlite3.Row | dict[str, Any]) -> int:
        exact = str(row["points_text"]) if "points_text" in row.keys() else "0"
        legacy = int(row["points"]) if "points" in row.keys() else 0
        if exact == "0" and legacy != 0:
            return legacy
        return int(exact or "0")

    @staticmethod
    def _write_points(db: sqlite3.Connection, uid: str, value: int, *, now: float | None = None) -> None:
        exact = int(value)
        legacy = min(exact, MAX_SQLITE_INTEGER)
        if now is None:
            db.execute("UPDATE users SET points=?,points_text=? WHERE uid=?", (legacy, str(exact), str(uid)))
        else:
            db.execute(
                "UPDATE users SET points=?,points_text=?,last_seen=? WHERE uid=?",
                (legacy, str(exact), float(now), str(uid)),
            )

    def cleanup_expired(self, now: float | None = None) -> dict[str, int]:
        from stella.retention import cleanup_database_retention

        stamp = time.time() if now is None else float(now)
        with self._session(self.connect()) as db:
            return cleanup_database_retention(db, now=stamp, today=date.today())


    def touch_user(self, uid: str, nickname: str = "Usuario", now: float | None = None) -> None:
        stamp = time.time() if now is None else float(now)
        with self._session(self.connect()) as db:
            db.execute(
                """INSERT INTO users(uid,nickname,created_at,last_seen)
                   VALUES(?,?,?,?) ON CONFLICT(uid) DO UPDATE SET
                   nickname=excluded.nickname,last_seen=excluded.last_seen""",
                (str(uid), str(nickname)[:80], stamp, stamp),
            )

    def get_profile(self, uid: str) -> dict[str, Any] | None:
        with self._session(self.connect()) as db:
            row = db.execute(
                "SELECT uid,nickname,bio,status,points,points_text,created_at,last_seen FROM users WHERE uid=?",
                (str(uid),),
            ).fetchone()
            if not row:
                return None
            profile = dict(row)
            profile["points"] = self._read_points(row)
            return profile

    def update_profile(self, uid: str, *, bio: str | None = None, status: str | None = None) -> bool:
        fields = []
        values: list[Any] = []
        if bio is not None:
            fields.append("bio=?")
            values.append(str(bio)[:300])
        if status is not None:
            fields.append("status=?")
            values.append(str(status)[:80])
        if not fields:
            return False
        fields.append("profile_updated_at=?")
        values.extend([time.time(), str(uid)])
        with self._session(self.connect()) as db:
            cursor = db.execute(f"UPDATE users SET {','.join(fields)} WHERE uid=?", values)
            return cursor.rowcount > 0

    def points_balance(self, uid: str) -> int:
        with self._session(self.connect()) as db:
            row = db.execute("SELECT points,points_text FROM users WHERE uid=?", (str(uid),)).fetchone()
            return self._read_points(row) if row else 0

    def claim_reward(self, uid: str, action: str, amount: int, cooldown: float) -> tuple[bool, int]:
        try:
            reward, wait = int(amount), float(cooldown)
        except (TypeError, ValueError, OverflowError):
            return False, -1
        if not str(uid).strip() or not str(action).strip() or reward <= 0 or wait <= 0:
            return False, -1
        now = time.time()
        with self._session(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            user = db.execute("SELECT points,points_text FROM users WHERE uid=?", (str(uid),)).fetchone()
            if not user:
                return False, -1
            balance = self._read_points(user)
            row = db.execute(
                "SELECT claimed_at FROM reward_claims WHERE uid=? AND action=?",
                (str(uid), str(action)),
            ).fetchone()
            if row and now - float(row["claimed_at"]) < wait:
                return False, max(1, int(wait - (now - float(row["claimed_at"]))))
            new_balance = balance + reward
            db.execute(
                """INSERT INTO reward_claims(uid,action,claimed_at) VALUES(?,?,?)
                   ON CONFLICT(uid,action) DO UPDATE SET claimed_at=excluded.claimed_at""",
                (str(uid), str(action), now),
            )
            self._write_points(db, uid, new_balance, now=now)
            return True, new_balance

    def grant_points(self, uid: str, nickname: str, amount: int) -> tuple[str, int]:
        try:
            value = int(amount)
        except (TypeError, ValueError, OverflowError):
            return "invalid", 0
        if not str(uid).strip() or value <= 0:
            return "invalid", 0
        now = time.time()
        with self._session(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT points,points_text FROM users WHERE uid=?", (str(uid),)).fetchone()
            if row:
                balance = self._read_points(row) + value
                db.execute(
                    "UPDATE users SET nickname=?,last_seen=? WHERE uid=?",
                    (str(nickname)[:80] or "Usuario", now, str(uid)),
                )
                self._write_points(db, uid, balance, now=now)
                return "ok", balance
            db.execute(
                """INSERT INTO users(uid,nickname,points,points_text,created_at,last_seen)
                   VALUES(?,?,?,?,?,?)""",
                (str(uid), str(nickname)[:80] or "Usuario",
                 min(value, MAX_SQLITE_INTEGER), str(value), now, now),
            )
            return "ok", value

    def transfer_points(self, sender_uid: str, target_uid: str, amount: int) -> str:
        try:
            value = int(amount)
        except (TypeError, ValueError, OverflowError):
            return "invalid"
        if value <= 0 or sender_uid == target_uid:
            return "invalid"
        with self._session(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            sender = db.execute("SELECT points,points_text FROM users WHERE uid=?", (str(sender_uid),)).fetchone()
            target = db.execute("SELECT points,points_text FROM users WHERE uid=?", (str(target_uid),)).fetchone()
            if not sender or not target:
                return "missing"
            sender_balance = self._read_points(sender)
            if sender_balance < value:
                return "insufficient"
            self._write_points(db, sender_uid, sender_balance - value)
            self._write_points(db, target_uid, self._read_points(target) + value)
            return "ok"

    @staticmethod
    def _expire_point_wagers(db: sqlite3.Connection, now: float) -> int:
        rows = db.execute(
            "SELECT id,challenger_uid,stake FROM point_wagers WHERE status='pending' AND expires_at<=?",
            (now,),
        ).fetchall()
        for row in rows:
            user = db.execute(
                "SELECT points,points_text FROM users WHERE uid=?", (str(row["challenger_uid"]),)
            ).fetchone()
            if user:
                Store._write_points(db, row["challenger_uid"], Store._read_points(user) + int(row["stake"]))
            db.execute("UPDATE point_wagers SET status='expired',settled_at=? WHERE id=?", (now, int(row["id"])))
        return len(rows)

    def create_point_wager(self, room_id: str, challenger_uid: str, challenger_name: str,
                           target_uid: str, target_name: str, stake: int) -> tuple[str, int, int]:
        if stake < 10 or stake > 100000 or str(challenger_uid) == str(target_uid):
            return "invalid", 0, self.points_balance(challenger_uid)
        now = time.time()
        with self._session(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire_point_wagers(db, now)
            challenger = db.execute("SELECT points,points_text FROM users WHERE uid=?", (str(challenger_uid),)).fetchone()
            target = db.execute("SELECT uid FROM users WHERE uid=?", (str(target_uid),)).fetchone()
            if not challenger or not target:
                return "missing", 0, self._read_points(challenger) if challenger else 0
            balance = self._read_points(challenger)
            if balance < stake:
                return "insufficient", 0, balance
            self._write_points(db, challenger_uid, balance - stake, now=now)
            cursor = db.execute(
                """INSERT INTO point_wagers(room_id,challenger_uid,challenger_name,target_uid,target_name,stake,created_at,expires_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (str(room_id), str(challenger_uid), str(challenger_name)[:80], str(target_uid),
                 str(target_name)[:80], stake, now, now + 600),
            )
            return "ok", int(cursor.lastrowid), balance - stake

    def accept_point_wager(self, room_id: str, wager_id: int, target_uid: str,
                           challenger_roll: int, target_roll: int) -> tuple[str, dict[str, Any]]:
        now = time.time()
        with self._session(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire_point_wagers(db, now)
            row = db.execute("SELECT * FROM point_wagers WHERE id=? AND room_id=?", (int(wager_id), str(room_id))).fetchone()
            if not row:
                return "missing", {}
            wager = dict(row)
            if wager["status"] != "pending":
                return "closed", wager
            if str(wager["target_uid"]) != str(target_uid):
                return "not_target", wager
            target = db.execute("SELECT points,points_text FROM users WHERE uid=?", (str(target_uid),)).fetchone()
            if not target:
                return "missing_user", wager
            stake = int(wager["stake"])
            target_balance = self._read_points(target)
            if target_balance < stake:
                return "insufficient", wager
            challenger_uid = str(wager["challenger_uid"])
            challenger = db.execute("SELECT points,points_text FROM users WHERE uid=?", (challenger_uid,)).fetchone()
            if not challenger:
                return "missing_user", wager
            self._write_points(db, target_uid, target_balance - stake, now=now)
            challenger_balance = self._read_points(challenger)
            if challenger_roll == target_roll:
                self._write_points(db, challenger_uid, challenger_balance + stake, now=now)
                self._write_points(db, target_uid, target_balance, now=now)
                winner_uid, status = None, "tie"
            else:
                winner_uid = challenger_uid if challenger_roll > target_roll else str(target_uid)
                winning_balance = challenger_balance if winner_uid == challenger_uid else target_balance - stake
                self._write_points(db, winner_uid, winning_balance + stake * 2, now=now)
                status = "settled"
            db.execute(
                """UPDATE point_wagers SET status=?,challenger_roll=?,target_roll=?,winner_uid=?,settled_at=? WHERE id=?""",
                (status, int(challenger_roll), int(target_roll), winner_uid, now, int(wager_id)),
            )
            wager.update({"status": status, "challenger_roll": int(challenger_roll),
                          "target_roll": int(target_roll), "winner_uid": winner_uid})
            return "ok", wager

    def reject_point_wager(self, room_id: str, wager_id: int, target_uid: str) -> tuple[str, dict[str, Any]]:
        now = time.time()
        with self._session(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire_point_wagers(db, now)
            row = db.execute("SELECT * FROM point_wagers WHERE id=? AND room_id=?", (int(wager_id), str(room_id))).fetchone()
            if not row:
                return "missing", {}
            wager = dict(row)
            if wager["status"] != "pending":
                return "closed", wager
            if str(wager["target_uid"]) != str(target_uid):
                return "not_target", wager
            user = db.execute("SELECT points,points_text FROM users WHERE uid=?", (str(wager["challenger_uid"]),)).fetchone()
            if user:
                self._write_points(db, wager["challenger_uid"], self._read_points(user) + int(wager["stake"]))
            db.execute("UPDATE point_wagers SET status='rejected',settled_at=? WHERE id=?", (now, int(wager_id)))
            wager["status"] = "rejected"
            return "ok", wager

    def point_wager_history(self, uid: str, limit: int = 5) -> list[dict[str, Any]]:
        with self._session(self.connect()) as db:
            rows = db.execute(
                """SELECT id,challenger_name,target_name,stake,status,challenger_roll,target_roll,winner_uid,created_at
                   FROM point_wagers WHERE challenger_uid=? OR target_uid=? ORDER BY id DESC LIMIT ?""",
                (str(uid), str(uid), max(1, min(10, int(limit)))),
            ).fetchall()
            return [dict(row) for row in rows]

    def settle_roulette(self, uid: str, wager: int, payout: int) -> tuple[str, int]:
        if wager <= 0 or payout < 0 or wager > 100000 or payout > 3600000:
            return "invalid", self.points_balance(uid)
        with self._session(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT points,points_text FROM users WHERE uid=?", (str(uid),)).fetchone()
            if not row:
                return "missing", 0
            balance = self._read_points(row)
            if balance < wager:
                return "insufficient", balance
            new_balance = balance - wager + payout
            self._write_points(db, uid, new_balance, now=time.time())
            return "ok", new_balance

    def points_ranking(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._session(self.connect()) as db:
            rows = db.execute(
                """SELECT nickname,points,points_text FROM users
                   ORDER BY length(points_text) DESC,points_text DESC,last_seen DESC LIMIT ?""",
                (max(1, min(20, int(limit))),),
            ).fetchall()
            return [{"nickname": row["nickname"], "points": self._read_points(row)} for row in rows]

    def add_affinity(self, uid: str, target_uid: str, amount: int = 1) -> int:
        if uid == target_uid:
            return 0
        now = time.time()
        with self._session(self.connect()) as db:
            db.execute(
                """INSERT INTO affinities(uid,target_uid,score,last_at) VALUES(?,?,?,?)
                   ON CONFLICT(uid,target_uid) DO UPDATE SET
                   score=MIN(100,affinities.score+excluded.score),last_at=excluded.last_at""",
                (str(uid), str(target_uid), max(1, min(5, int(amount))), now),
            )
            row = db.execute(
                "SELECT score FROM affinities WHERE uid=? AND target_uid=?",
                (str(uid), str(target_uid)),
            ).fetchone()
            return int(row["score"]) if row else 0

    def get_affinity(self, uid: str, target_uid: str) -> int:
        with self._session(self.connect()) as db:
            row = db.execute(
                "SELECT score FROM affinities WHERE uid=? AND target_uid=?",
                (str(uid), str(target_uid)),
            ).fetchone()
            return int(row["score"]) if row else 0

    def add_gift(self, sender_uid: str, sender_name: str, target_uid: str, target_name: str, message: str = "") -> int:
        now = time.time()
        with self._session(self.connect()) as db:
            cursor = db.execute(
                """INSERT INTO gifts(sender_uid,sender_name,target_uid,target_name,message,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (str(sender_uid), str(sender_name)[:80], str(target_uid), str(target_name)[:80], str(message)[:160], now),
            )
            db.execute(
                """DELETE FROM gifts WHERE target_uid=? AND id NOT IN
                   (SELECT id FROM gifts WHERE target_uid=? ORDER BY id DESC LIMIT 5)""",
                (str(target_uid), str(target_uid)),
            )
            return int(cursor.lastrowid)

    def received_gifts(self, uid: str, limit: int = 5) -> list[dict[str, Any]]:
        with self._session(self.connect()) as db:
            rows = db.execute(
                """SELECT sender_name,message,created_at FROM gifts
                   WHERE target_uid=? AND created_at>=? ORDER BY id DESC LIMIT ?""",
                (str(uid), time.time() - 30 * 86400, max(1, min(5, int(limit)))),
            ).fetchall()
            return [dict(row) for row in rows]


    def create_playlist(self, room_id: str, owner_uid: str, name: str) -> bool:
        clean = " ".join(str(name or "").split())[:48]
        if not clean:
            return False
        with self._session(self.connect()) as db:
            exists = db.execute(
                "SELECT 1 FROM dj_playlists WHERE room_id=? AND owner_uid=? AND name=? COLLATE NOCASE",
                (str(room_id), str(owner_uid), clean),
            ).fetchone()
            if exists:
                return False
            try:
                db.execute(
                    "INSERT INTO dj_playlists(room_id,owner_uid,name,created_at) VALUES(?,?,?,?)",
                    (str(room_id), str(owner_uid), clean, time.time()),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def list_playlists(self, room_id: str, owner_uid: str) -> list[dict[str, Any]]:
        with self._session(self.connect()) as db:
            rows = db.execute(
                """SELECT p.id,p.name,p.created_at,COUNT(t.id) AS tracks
                   FROM dj_playlists p LEFT JOIN dj_playlist_tracks t ON t.playlist_id=p.id
                   WHERE p.room_id=? AND p.owner_uid=? GROUP BY p.id ORDER BY p.name COLLATE NOCASE""",
                (str(room_id), str(owner_uid)),
            ).fetchall()
            return [dict(row) for row in rows]

    def add_playlist_track(self, room_id: str, owner_uid: str, name: str, track: dict[str, Any]) -> str:
        with self._session(self.connect()) as db:
            playlist = db.execute(
                "SELECT id FROM dj_playlists WHERE room_id=? AND owner_uid=? AND name=? COLLATE NOCASE",
                (str(room_id), str(owner_uid), str(name).strip()),
            ).fetchone()
            if not playlist:
                return "missing"
            count = db.execute("SELECT COUNT(*) AS n FROM dj_playlist_tracks WHERE playlist_id=?", (playlist["id"],)).fetchone()["n"]
            if int(count) >= 40:
                return "full"
            required = ("title", "creator", "license", "source", "url", "item_url")
            if any(not str(track.get(key) or "").strip() for key in required):
                return "invalid"
            db.execute(
                """INSERT INTO dj_playlist_tracks
                   (playlist_id,title,creator,license,source,url,item_url,identifier,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (playlist["id"], *(str(track.get(key) or "")[:1000] for key in required),
                 str(track.get("identifier") or "")[:120], time.time()),
            )
            return "ok"

    def playlist_tracks(self, room_id: str, owner_uid: str, name: str) -> list[dict[str, Any]]:
        with self._session(self.connect()) as db:
            playlist = db.execute(
                "SELECT id FROM dj_playlists WHERE room_id=? AND owner_uid=? AND name=? COLLATE NOCASE",
                (str(room_id), str(owner_uid), str(name).strip()),
            ).fetchone()
            if not playlist:
                return []
            rows = db.execute(
                """SELECT title,creator,license,source,url,item_url,identifier
                   FROM dj_playlist_tracks WHERE playlist_id=? ORDER BY id LIMIT 40""",
                (playlist["id"],),
            ).fetchall()
            return [dict(row) for row in rows]

    def remove_playlist_track(self, room_id: str, owner_uid: str, name: str, position: int) -> bool:
        """Remove one 1-based playlist position, scoped to its owning user and room."""
        try:
            index = int(position)
        except (TypeError, ValueError):
            return False
        if index < 1:
            return False
        with self._session(self.connect()) as db:
            playlist = db.execute(
                "SELECT id FROM dj_playlists WHERE room_id=? AND owner_uid=? AND name=? COLLATE NOCASE",
                (str(room_id), str(owner_uid), str(name).strip()),
            ).fetchone()
            if not playlist:
                return False
            row = db.execute(
                "SELECT id FROM dj_playlist_tracks WHERE playlist_id=? ORDER BY id LIMIT 1 OFFSET ?",
                (playlist["id"], index - 1),
            ).fetchone()
            if not row:
                return False
            db.execute("DELETE FROM dj_playlist_tracks WHERE id=? AND playlist_id=?", (row["id"], playlist["id"]))
            return True

    def delete_playlist(self, room_id: str, owner_uid: str, name: str) -> bool:
        with self._session(self.connect()) as db:
            row = db.execute(
                "SELECT id FROM dj_playlists WHERE room_id=? AND owner_uid=? AND name=? COLLATE NOCASE",
                (str(room_id), str(owner_uid), str(name).strip()),
            ).fetchone()
            if not row:
                return False
            db.execute("DELETE FROM dj_playlist_tracks WHERE playlist_id=?", (row["id"],))
            db.execute("DELETE FROM dj_playlists WHERE id=?", (row["id"],))
            return True

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

    def activity_summary(self, room_id: str, limit: int = 10, days: int = 7) -> list[dict[str, Any]]:
        from datetime import datetime, timedelta, timezone

        period = max(1, min(7, int(days)))
        today = datetime.now(timezone.utc).date()
        first_day = (today - timedelta(days=period - 1)).isoformat()
        last_day = today.isoformat()
        with self._session(self.connect()) as db:
            rows = db.execute(
                """SELECT user_uid,SUM(message_count) AS total_messages
                   FROM activity_daily
                   WHERE room_id=? AND day_key>=? AND day_key<=?
                   GROUP BY user_uid ORDER BY total_messages DESC LIMIT ?""",
                (str(room_id), first_day, last_day, max(1, min(20, int(limit)))),
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
