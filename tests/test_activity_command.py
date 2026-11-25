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
