import asyncio
from types import SimpleNamespace

from stella.bot import StellaBot


def make_bot():
    bot = object.__new__(StellaBot)
    bot.replies = []
    bot.ai_core = SimpleNamespace(translate=None)
    bot.services = SimpleNamespace(ai_request=object())
    async def reply(room_id, content, message):
        bot.replies.append(content)
    bot._reply = reply
    return bot


def test_translate_validates_format_and_calls_ai_core():
    bot = make_bot()
    calls = []
    async def translate(language, content, request_func, rate_key=None):
        calls.append((language, content, rate_key))
        return "Hello world"
    bot.ai_core.translate = translate
    asyncio.run(bot._translate("room", "user-1", "User", "English | Hola mundo", {}))
    assert calls == [("English", "Hola mundo", "user-1")]
    assert bot.replies == ["Hello world"]


def test_translate_rejects_missing_separator_or_empty_text():
    bot = make_bot()
    asyncio.run(bot._translate("room", "user-1", "User", "English", {}))
    assert bot.replies == ["Uso: !traducir <idioma> | <texto>"]


def test_translate_rejects_oversized_text():
    bot = make_bot()
    asyncio.run(bot._translate("room", "user-1", "User", "English | " + ("x" * 2501), {}))
    assert "2500 caracteres" in bot.replies[0]
