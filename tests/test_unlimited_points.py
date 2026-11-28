from stella.storage import Store


def test_owner_grant_supports_balances_far_beyond_sqlite_integer(tmp_path):
    store = Store(tmp_path / "huge-points.sqlite3")
    store.initialize()
    store.touch_user("member", "Miembro")

    huge = 10**120
    status, balance = store.grant_points("member", "Miembro", huge)

    assert status == "ok"
    assert balance == huge
    assert store.points_balance("member") == huge
    assert store.get_profile("member")["points"] == huge
    assert store.points_ranking()[0]["points"] == huge


def test_existing_sqlite_database_migrates_legacy_points_without_loss(tmp_path):
    import sqlite3
    import time

    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            """CREATE TABLE users (
                uid TEXT PRIMARY KEY, nickname TEXT NOT NULL,
                bio TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '',
                points INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
                last_seen REAL NOT NULL, profile_updated_at REAL
            )"""
        )
        db.execute(
            "INSERT INTO users(uid,nickname,points,created_at,last_seen) VALUES(?,?,?,?,?)",
            ("legacy", "Antiguo", 123456789, time.time(), time.time()),
        )

    store = Store(path)
    store.initialize()
    assert store.points_balance("legacy") == 123456789

    status, balance = store.grant_points("legacy", "Antiguo", 10**90)
    assert status == "ok"
    assert balance == 123456789 + 10**90
    assert store.points_balance("legacy") == balance


def test_unbounded_balances_work_with_rewards_and_transfers(tmp_path):
    store = Store(tmp_path / "big-economy.sqlite3")
    store.initialize()
    store.touch_user("sender", "Emisor")
    store.touch_user("target", "Destino")

    huge = 10**100
    assert store.grant_points("sender", "Emisor", huge) == ("ok", huge)
    assert store.transfer_points("sender", "target", huge - 1) == "ok"
    assert store.points_balance("sender") == 1
    assert store.points_balance("target") == huge - 1

    ok, balance = store.claim_reward("target", "daily", 100, 86400)
    assert ok is True
    assert balance == huge - 1 + 100

def test_owner_command_accepts_arbitrarily_large_decimal_amount(tmp_path):
    import asyncio
    from types import SimpleNamespace

    from stella.bot import StellaBot

    store = Store(tmp_path / "owner-command.sqlite3")
    store.initialize()
    bot = object.__new__(StellaBot)
    bot.store = store
    bot.settings = SimpleNamespace(command_prefix="!")
    bot._is_owner = lambda uid: uid == "owner"
    bot._mentioned_user = lambda message: ("member", "Miembro")
    replies = []

    async def reply(room_id, content, message):
        replies.append(content)

    bot._reply = reply
    amount = 10**120
    asyncio.run(
        bot._give_points(
            "room", "owner", "Propietario", f"@Miembro {amount}", {}
        )
    )

    assert store.points_balance("member") == amount
    assert str(amount) in replies[-1]

