import asyncio
from types import SimpleNamespace

from stella.bot import StellaBot


class ActivityStore:
    def __init__(self, rows):
        self.rows = rows
        self.requested_days = None

    def activity_summary(self, room_id, days=7):
        self.requested_days = days
        return self.rows


def make_bot(rows):
    bot = object.__new__(StellaBot)
    bot.store = ActivityStore(rows)
    bot.replies = []
    async def reply(room_id, content, message):
        bot.replies.append((room_id, content))
    bot._reply = reply
    return bot


def test_activity_is_available_without_admin_and_does_not_expose_uids():
    bot = make_bot([
        {"user_uid":"private-user-1","total_messages":12},
        {"user_uid":"private-user-2","total_messages":8},
    ])
    asyncio.run(bot._activity("room","ordinary-user","Person","hoy",{}))
    assert bot.store.requested_days == 1
    assert "20" in bot.replies[0][1]
    assert "2" in bot.replies[0][1]
    assert "private-user" not in bot.replies[0][1]


def test_activity_defaults_to_seven_days():
    bot = make_bot([{"user_uid":"private-user","total_messages":4}])
    asyncio.run(bot._activity("room","ordinary-user","Person","",{}))
    assert bot.store.requested_days == 7



def test_payment_rejects_zero_and_negative_amounts_without_transfer():
    class PaymentStore:
        def __init__(self):
            self.calls = []

        def transfer_points(self, *args):
            self.calls.append(args)
            return "ok"

        def points_balance(self, uid):
            return 90

    bot = object.__new__(StellaBot)
    bot.store = PaymentStore()
    bot.replies = []

    async def reply(room_id, content, message):
        bot.replies.append(content)

    bot._reply = reply
    message = {"mentions": [{"uid": "target", "nickname": "Destino"}]}

    asyncio.run(bot._pay("room", "sender", "Emisor", "0", message))
    asyncio.run(bot._pay("room", "sender", "Emisor", "-25", message))

    assert bot.store.calls == []
    assert "mayor que cero" in bot.replies[0]
    assert "Uso:" in bot.replies[1]


def test_payment_success_reports_updated_balance():
    class PaymentStore:
        def transfer_points(self, sender, target, amount):
            assert (sender, target, amount) == ("sender", "target", 25)
            return "ok"

        def points_balance(self, uid):
            return 75

    bot = object.__new__(StellaBot)
    bot.store = PaymentStore()
    bot.replies = []

    async def reply(room_id, content, message):
        bot.replies.append(content)

    bot._reply = reply
    message = {"mentions": [{"uid": "target", "nickname": "Destino"}]}

    asyncio.run(bot._pay("room", "sender", "Emisor", "25", message))

    assert "Transferencia realizada: 25 puntos para Destino." in bot.replies[0]
    assert "saldo actual: 75 puntos" in bot.replies[0].casefold()


def test_trivia_rejects_out_of_range_answers_without_ending_game():
    import time

    bot = object.__new__(StellaBot)
    bot._game_sessions = {
        "room": {"kind": "trivia", "expires": time.monotonic() + 60, "answer": 1}
    }
    bot.replies = []

    async def reply(room_id, content, message):
        bot.replies.append(content)

    bot._reply = reply

    asyncio.run(bot._trivia_answer("room", "u1", "Persona", "9", {}))

    assert "no existe" in bot.replies[0].casefold()
    assert "room" in bot._game_sessions
