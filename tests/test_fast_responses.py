import asyncio
from types import SimpleNamespace

from stella.bot import StellaBot
from stella.services import StellaServices
from stella.ai_core import StellaAICore


def test_memory_capture_skips_remote_preference_lookup_for_ordinary_message():
    class Memory:
        def extract_explicit_facts(self, value):
            return []

        async def remember_from_message(self, *args):
            raise AssertionError("ordinary messages must not reach Qdrant")

    class Store:
        def get_setting(self, *args):
            return "0"

    bot = object.__new__(StellaBot)
    bot.ai_memory = Memory()
    bot.store = Store()

    result = asyncio.run(bot._remember_ai_facts("room", "member", "Hola, ¿cómo están?"))

    assert result == []


def test_background_work_is_scheduled_without_waiting_for_completion():
    bot = object.__new__(StellaBot)
    completed = []

    async def work():
        await asyncio.sleep(0)
        completed.append(True)

    async def scenario():
        bot._schedule_background(work(), label="test")
        assert not completed
        await asyncio.gather(*bot._background_tasks)
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert completed == [True]
    assert bot._background_tasks == set()


def test_personal_context_is_restored_in_one_fast_memory_read():
    class Memory:
        def __init__(self):
            self.calls = 0

        async def load_context(self, room_id, uid):
            self.calls += 1
            return (
                [{"role": "user", "content": "Seguimos con el proyecto."}],
                [{"topic": "proyecto", "content": "Atlas"}],
            )

    class Core:
        def __init__(self):
            self.turns = []

        def _history(self, key):
            return self.turns

        def _append_history(self, key, role, content):
            self.turns.append({"role": role, "content": content})

    class Store:
        def get_setting(self, room_id, key, default="0"):
            return "0"

    bot = object.__new__(StellaBot)
    bot.ai_memory = Memory()
    bot.ai_core = Core()
    bot.store = Store()

    facts = asyncio.run(bot._load_ai_context("room", "member", "room:member"))
    assert facts == [{"topic": "proyecto", "content": "Atlas"}]
    assert bot.ai_memory.calls == 1
    assert bot.ai_core.turns == [
        {"role": "user", "content": "Seguimos con el proyecto."}
    ]


def test_gemini_request_spacing_no_longer_adds_a_three_second_floor():
    assert StellaAICore.provider_default_min_interval["Google Gemini"] == 1.0


def test_ai_http_requests_reuse_one_keepalive_client(monkeypatch):
    created = []

    class FakeResponse:
        status_code = 200

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.is_closed = False
            self.calls = []
            created.append(self)

        async def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return FakeResponse()

        async def aclose(self):
            self.is_closed = True

    monkeypatch.setattr("stella.services.httpx.AsyncClient", FakeClient)
    services = StellaServices()

    async def scenario():
        first = await services.ai_request("POST", "https://example.test/first", json={"a": 1})
        second = await services.ai_request("POST", "https://example.test/second", json={"b": 2})
        assert first.status_code == second.status_code == 200
        assert len(created) == 1
        assert len(created[0].calls) == 2
        assert created[0].kwargs["limits"].max_keepalive_connections == 15
        await services.close()
        assert created[0].is_closed

    asyncio.run(scenario())
