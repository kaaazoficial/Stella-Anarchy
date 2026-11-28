import asyncio
import random

from stella.bot import StellaBot
from stella.storage import Store


def make_bot(store):
    bot = object.__new__(StellaBot)
    bot.store = store
    bot.settings = type("Settings", (), {"command_prefix": "!"})()
    bot.replies = []

    async def reply(room_id, content, message):
        bot.replies.append(content)

    bot._reply = reply
    return bot


def make_store(tmp_path, balance=100):
    store = Store(tmp_path / "roulette.sqlite3")
    store.initialize()
    with store._session(store.connect()) as db:
        db.execute(
            "INSERT INTO users(uid,nickname,points,created_at,last_seen) VALUES(?,?,?,?,?)",
            ("user", "Persona", balance, 1, 1),
        )
    return store


def test_roulette_red_win_pays_even_money_and_updates_balance(tmp_path, monkeypatch):
    store = make_store(tmp_path, 100)
    bot = make_bot(store)
    monkeypatch.setattr(random, "randint", lambda low, high: 1)

    asyncio.run(bot._roulette("room", "user", "Persona", "20 rojo", {}))

    assert store.points_balance("user") == 120
    assert "1 🔴 ROJO" in bot.replies[0]
    assert "GANASTE +20 puntos" in bot.replies[0]


def test_roulette_zero_loses_even_money_bet(tmp_path, monkeypatch):
    store = make_store(tmp_path, 100)
    bot = make_bot(store)
    monkeypatch.setattr(random, "randint", lambda low, high: 0)

    asyncio.run(bot._roulette("room", "user", "Persona", "20 rojo", {}))

    assert store.points_balance("user") == 80
    assert "0 🟢 VERDE" in bot.replies[0]


def test_roulette_exact_number_pays_35_to_1_net(tmp_path, monkeypatch):
    store = make_store(tmp_path, 100)
    bot = make_bot(store)
    monkeypatch.setattr(random, "randint", lambda low, high: 17)

    asyncio.run(bot._roulette("room", "user", "Persona", "2 17", {}))

    assert store.points_balance("user") == 170
    assert "GANASTE +70 puntos" in bot.replies[0]


def test_roulette_rejects_invalid_bet_without_charging(tmp_path):
    store = make_store(tmp_path, 100)
    bot = make_bot(store)

    asyncio.run(bot._roulette("room", "user", "Persona", "20 azul", {}))

    assert store.points_balance("user") == 100
    assert "Apuesta no válida" in bot.replies[0]


def test_roulette_rejects_insufficient_balance_without_charging(tmp_path, monkeypatch):
    store = make_store(tmp_path, 10)
    bot = make_bot(store)
    monkeypatch.setattr(random, "randint", lambda low, high: 1)

    asyncio.run(bot._roulette("room", "user", "Persona", "20 rojo", {}))

    assert store.points_balance("user") == 10
    assert "No tienes suficientes puntos" in bot.replies[0]


def test_roulette_rejects_zero_stake_without_charging(tmp_path):
    store = make_store(tmp_path, 100)
    bot = make_bot(store)

    asyncio.run(bot._roulette("room", "user", "Persona", "0 rojo", {}))

    assert store.points_balance("user") == 100
    assert "entre 1 y 100.000" in bot.replies[0]
