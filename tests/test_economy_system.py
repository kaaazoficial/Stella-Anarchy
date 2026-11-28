import asyncio
import random

from stella.bot import StellaBot
from stella.storage import Store


def make_store(tmp_path, balances=(("owner", "Dueño", 0), ("challenger", "Reto", 500), ("target", "Objetivo", 500))):
    store = Store(tmp_path / "economy.sqlite3")
    store.initialize()
    for uid, name, points in balances:
        store.touch_user(uid, name)
        with store._session(store.connect()) as db:
            db.execute("UPDATE users SET points=? WHERE uid=?", (points, uid))
    return store


def make_bot(store, owner_uid="owner"):
    bot = object.__new__(StellaBot)
    bot.store = store
    bot.settings = type("Settings", (), {"command_prefix": "!", "sinner_uid": owner_uid})()
    bot.replies = []

    async def reply(room_id, content, message):
        bot.replies.append(content)

    bot._reply = reply
    bot._mentioned_user = lambda message: message.get("target")
    return bot


def test_claim_reward_is_atomic_and_obeys_cooldown(tmp_path):
    store = make_store(tmp_path, (("member", "Miembro", 0),))
    ok, balance = store.claim_reward("member", "daily", 100, 86400)
    assert (ok, balance) == (True, 100)
    ok, remaining = store.claim_reward("member", "daily", 100, 86400)
    assert ok is False
    assert 0 < remaining <= 86400
    assert store.points_balance("member") == 100


def test_claim_reward_missing_profile_does_not_consume_claim(tmp_path):
    store = make_store(tmp_path, ())
    ok, status = store.claim_reward("missing", "daily", 100, 86400)
    assert (ok, status) == (False, -1)
    store.touch_user("missing", "Miembro")
    ok, balance = store.claim_reward("missing", "daily", 100, 86400)
    assert (ok, balance) == (True, 100)


def test_daily_and_work_handlers_deliver_points_and_report_cooldown(tmp_path, monkeypatch):
    store = make_store(tmp_path, (("member", "Miembro", 0),))
    bot = make_bot(store)
    monkeypatch.setattr(random, "randint", lambda low, high: 30)

    asyncio.run(bot._daily("room", "member", "Miembro", "", {}))
    assert store.points_balance("member") == 100
    assert "+100 puntos" in bot.replies[-1]
    asyncio.run(bot._daily("room", "member", "Miembro", "", {}))
    assert "Ya reclamaste" in bot.replies[-1]

    asyncio.run(bot._work("room", "member", "Miembro", "", {}))
    assert store.points_balance("member") == 130
    assert "+30 puntos" in bot.replies[-1]
    asyncio.run(bot._work("room", "member", "Miembro", "", {}))
    assert "descansar" in bot.replies[-1]


def test_bet_challenge_parses_stake_and_reserves_it(tmp_path):
    store = make_store(tmp_path)
    bot = make_bot(store)
    message = {"target": ("target", "Objetivo")}
    asyncio.run(bot._bet_challenge("room", "challenger", "Reto", " @Objetivo 125", message))
    assert store.points_balance("challenger") == 375
    row = store.point_wager_history("challenger")[0]
    assert row["stake"] == 125
    assert row["status"] == "pending"
    assert "\\n" not in bot.replies[-1]
    assert "10 rondas" in bot.replies[-1]


def test_bet_accept_runs_ten_rounds_and_settles_tie(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    bot = make_bot(store)
    status, wager_id, _ = store.create_point_wager("room", "challenger", "Reto", "target", "Objetivo", 100)
    assert status == "ok"
    rolls = []

    def fixed_die(low, high):
        rolls.append((low, high))
        return 6

    monkeypatch.setattr(random, "randint", fixed_die)
    asyncio.run(bot._accept_bet("room", "target", "Objetivo", str(wager_id), {}))
    assert len(rolls) == 40
    assert store.points_balance("challenger") == 500
    assert store.points_balance("target") == 500
    assert "/120" in bot.replies[-1]
    assert "EMPATE" in bot.replies[-1]


def test_owner_can_grant_arbitrary_points_and_create_target_profile(tmp_path):
    store = make_store(tmp_path, (("owner", "Dueño", 0),))
    bot = make_bot(store)
    message = {"target": ("new-member", "Nuevo")}
    asyncio.run(bot._give_points("room", "owner", "Dueño", "@Nuevo 900000000", message))
    assert store.points_balance("new-member") == 900000000
    assert "900000000 puntos" in bot.replies[-1]


def test_non_owner_cannot_grant_points(tmp_path):
    store = make_store(tmp_path)
    bot = make_bot(store)
    asyncio.run(bot._give_points("room", "challenger", "Reto", "@Objetivo 5000", {"target": ("target", "Objetivo")}))
    assert store.points_balance("target") == 500
    assert "reservado al propietario" in bot.replies[-1]


def test_owner_grant_rejects_invalid_amount_without_changing_balance(tmp_path):
    store = make_store(tmp_path)
    bot = make_bot(store)
    asyncio.run(bot._give_points("room", "owner", "Dueño", "@Objetivo 0", {"target": ("target", "Objetivo")}))
    assert store.points_balance("target") == 500
    assert "mayor que cero" in bot.replies[-1]
