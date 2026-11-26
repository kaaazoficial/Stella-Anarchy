from __future__ import annotations

import httpx
import pytest

from stella.shared_state import QdrantSharedState


def _qdrant_mock():
    collections: set[str] = set()
    points: dict[str, dict] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/collections/stella_global_state":
            if request.method == "GET":
                if "stella_global_state" not in collections:
                    return httpx.Response(404, request=request, json={"status": {"error": "not found"}})
                return httpx.Response(
                    200,
                    request=request,
                    json={"result": {"config": {"params": {"vectors": {"size": 1}}}}},
                )
            if request.method == "PUT":
                collections.add("stella_global_state")
                return httpx.Response(200, request=request, json={"result": True})

        if path.startswith("/collections/stella_global_state/points/") and request.method == "GET":
            point_id = path.rsplit("/", 1)[-1]
            point = points.get(point_id)
            return httpx.Response(200, request=request, json={"result": point})

        if path == "/collections/stella_global_state/points" and request.method == "PUT":
            payload = request.read()
            import json
            body = json.loads(payload)
            for point in body["points"]:
                points[point["id"]] = {"payload": point["payload"]}
            return httpx.Response(200, request=request, json={"result": {"status": "completed"}})

        return httpx.Response(404, request=request, json={"status": {"error": "not found"}})

    return httpx.MockTransport(handle)


@pytest.mark.asyncio
async def test_global_silence_setting_is_shared_between_instances():
    transport = _qdrant_mock()
    first_client = httpx.AsyncClient(transport=transport)
    second_client = httpx.AsyncClient(transport=transport)
    first = QdrantSharedState(
        url="https://qdrant.example",
        api_key="test-key",
        client=first_client,
    )
    second = QdrantSharedState(
        url="https://qdrant.example",
        api_key="test-key",
        client=second_client,
    )

    assert await second.get_setting("chat_silence") is None
    await first.set_setting("chat_silence", "1")
    assert await second.get_setting("chat_silence") == "1"
    await first.set_setting("chat_silence", "0")
    assert await second.get_setting("chat_silence") == "0"

    await first.close()
    await second.close()
    await first_client.aclose()
    await second_client.aclose()


@pytest.mark.asyncio
async def test_shared_state_fails_clearly_without_qdrant_credentials():
    state = QdrantSharedState(url="", api_key="")
    with pytest.raises(RuntimeError, match="Qdrant is not configured"):
        await state.get_setting("chat_silence")


def test_global_silence_commands_recognize_natural_activation_and_resume_phrases():
    from stella.bot import StellaBot

    activation_phrases = (
        "Stella, activa el silencio global.",
        "activa silencio global",
        "silencia todos los chats",
        "no hables en ningún chat",
        "no respondas en ningún chat",
    )
    resume_phrases = (
        "desactiva el silencio global",
        "desactiva silencio global",
        "reactiva el chat global",
        "vuelve a hablar en todos los chats",
        "ya puedes hablar en todos los chats",
    )

    assert all(StellaBot._is_silence_request(phrase) for phrase in activation_phrases)
    assert all(StellaBot._is_resume_request(phrase) for phrase in resume_phrases)
