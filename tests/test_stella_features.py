from __future__ import annotations

import time

import pytest
from pathlib import Path

from stella.ai import StellaAI
from stella.help import build_commands_help_text, build_commands_help_blocks, filter_commands_help_text
from stella.identity import extract_stella_call
from stella.research import classify_research_intent
from stella.storage import Store


def test_stella_help_catalog_has_general_categories_only():
    full = build_commands_help_text("!")
    assert "STELLA" in full
    assert "!perfil" in full
    assert "!diario" in full
    assert "!dj buscar" in full
    social = filter_commands_help_text(full, "social")
    assert "PERFILES" in social
    assert "!afinidad" in social
    assert "ECONOMÍA" not in social
    blocks = build_commands_help_blocks("!")
    assert len(blocks) == 6
    assert all(len(block.encode("utf-16-le")) // 2 <= 1800 for block in blocks)


def test_stella_identity_recognizes_direct_calls_without_casual_mentions():
    assert extract_stella_call("Oye Stella, ¿puedes ayudarme?") == "¿puedes ayudarme?"
    assert extract_stella_call("Stella revisa este texto") == "revisa este texto"
    assert extract_stella_call("Hablé con Stella ayer") is None


def test_research_intent_routes_general_topics():
    music = classify_research_intent("Investiga discografía y canciones de una banda")
    science = classify_research_intent("Investiga un estudio científico sobre astronomía")
    assert music["music"]
    assert science["academic"]
    assert science["nasa"]


def test_ai_context_is_ephemeral_and_can_be_forgotten():
    ai = StellaAI()
    ai._memory["room:user"] = {
        "turns": [{"role": "user", "content": "hola"}],
        "updated_at": time.monotonic(),
    }
    assert ai.forget_session("room:user")
    assert not ai.forget_session("room:user")
    ai._memory["expired"] = {
        "turns": [{"role": "user", "content": "old"}],
        "updated_at": time.monotonic() - ai.memory_ttl - 1,
    }
    ai._prune_memory()
    assert "expired" not in ai._memory


def test_music_playlists_store_only_validated_catalog_metadata(tmp_path: Path):
    store = Store(tmp_path / "stella.sqlite3")
    store.initialize()
    store.touch_user("u1", "Uno")
    assert store.create_playlist("room", "u1", "Favoritos")
    assert not store.create_playlist("room", "u1", "favoritos")
    track = {
        "title": "Instrumental track",
        "creator": "Artist",
        "license": "CC BY",
        "source": "Internet Archive",
        "url": "https://archive.org/download/example/track.mp3",
        "item_url": "https://archive.org/details/example",
        "identifier": "example",
    }
    assert store.add_playlist_track("room", "u1", "Favoritos", track) == "ok"
    rows = store.list_playlists("room", "u1")
    assert rows[0]["tracks"] == 1
    assert store.playlist_tracks("room", "u1", "Favoritos")[0]["title"] == "Instrumental track"
    assert store.delete_playlist("room", "u1", "Favoritos")
    assert store.list_playlists("room", "u1") == []


def test_music_mood_helper_normalizes_spanish_accents():
    from stella.astra import resolve_music_mood

    assert resolve_music_mood("concentración") == "instrumental focus study ambient"
    assert resolve_music_mood("dormir") == "soft ambient sleep meditation"


def test_profiles_economy_gifts_and_affinity_expire_operational_data(tmp_path: Path):
    store = Store(tmp_path / "stella.sqlite3")
    store.initialize()
    store.touch_user("u1", "Uno")
    store.touch_user("u2", "Dos")
    assert store.update_profile("u1", bio="Perfil temporal", status="Activo")
    profile = store.get_profile("u1")
    assert profile["bio"] == "Perfil temporal"
    assert profile["status"] == "Activo"

    claimed, balance = store.claim_reward("u1", "daily", 100, 86400)
    assert claimed and balance == 100
    claimed_again, remaining = store.claim_reward("u1", "daily", 100, 86400)
    assert not claimed_again and remaining > 0
    assert store.transfer_points("u1", "u2", 30) == "ok"
    assert store.points_balance("u1") == 70
    assert store.points_balance("u2") == 30

    store.add_gift("u1", "Uno", "u2", "Dos", "Un detalle")
    assert store.received_gifts("u2")[0]["message"] == "Un detalle"
    assert store.add_affinity("u1", "u2", 3) == 3
    assert store.get_affinity("u1", "u2") == 3

    old = time.time() - 31 * 86400
    with store._session(store.connect()) as db:
        db.execute("UPDATE users SET profile_updated_at=? WHERE uid='u1'", (old,))
        db.execute("UPDATE gifts SET created_at=? WHERE target_uid='u2'", (old,))
    store.cleanup_expired()
    assert store.get_profile("u1")["bio"] == ""
    assert store.received_gifts("u2") == []

def test_avatar_validation_rejects_unsupported_mime_and_invalid_hosts():
    import pytest

    from stella.images import validate_avatar_image_bytes
    from stella.services import StellaServices

    with pytest.raises(ValueError):
        validate_avatar_image_bytes(b"not-image", "text/html")
    services = StellaServices()
    async def check():
        with pytest.raises(ValueError):
            await services.fetch_avatar_image("https://example.com/avatar.png")
        await services.close()
    import asyncio
    asyncio.run(check())

def test_advanced_ai_core_is_stella_only_and_bounded():
    from stella.ai_core import AI_COMMAND_CATALOG, AI_PUBLIC_COMMANDS, StellaAICore

    core = StellaAICore()
    assert core.memory_ttl == 600
    assert core.memory_turns == 5
    assert "ayuda" in AI_PUBLIC_COMMANDS
    assert "investigar" in AI_COMMAND_CATALOG
    assert "" not in AI_PUBLIC_COMMANDS



def test_ship_verdict_changes_its_reaction_by_compatibility_score(monkeypatch):
    from stella.bot import ship_verdict

    monkeypatch.setattr("stella.bot.random.choice", lambda values: values[0])
    low = ship_verdict(5, "Ana", "Luis")
    medium = ship_verdict(50, "Ana", "Luis")
    high = ship_verdict(95, "Ana", "Luis")
    perfect = ship_verdict(100, "Ana", "Luis")

    assert "Compatibilidad: 5%" in low
    assert "Compatibilidad: 50%" in medium
    assert "Compatibilidad: 95%" in high
    assert "Compatibilidad: 100%" in perfect
    assert len({low.splitlines()[-1], medium.splitlines()[-1], high.splitlines()[-1], perfect.splitlines()[-1]}) == 4
    assert "ficticio" not in perfect.casefold()
    assert "recreativo" not in perfect.casefold()



def test_public_help_keeps_six_pages_without_administrative_commands():
    from stella.help import filter_commands_help_text

    full = build_commands_help_text("!")
    public = filter_commands_help_text(full, include_admin=False)
    admin_view = filter_commands_help_text(full, include_admin=True)

    assert "6. MEMORIA, CONTEXTO Y PRIVACIDAD" in public
    assert "!memoria recuerdos" in public
    assert "!historial" not in public
    assert "!idbot" not in public
    assert "!salas" not in public
    assert "[ADMIN]" not in public
    assert "6. ADMINISTRACIÓN Y ESTADO" not in public
    assert admin_view == public


def test_public_help_does_not_expose_admin_category_alias():
    from stella.help import filter_commands_help_text

    full = build_commands_help_text("!")
    result = filter_commands_help_text(full, "admin", include_admin=False)

    assert "6. ADMINISTRACIÓN" not in result
    assert "!antispam" not in result


@pytest.mark.asyncio
async def test_admin_replies_are_suppressed_in_public_rooms():
    from stella.bot import StellaBot

    class Platform:
        def __init__(self):
            self.sent = []
        async def send_message(self, *args, **kwargs):
            self.sent.append((args, kwargs))

    bot = object.__new__(StellaBot)
    bot.platform = Platform()
    await bot._reply("room-1", "Información administrativa privada", {
        "_stella_admin_silent": True,
        "messageId": "message-1",
    })

    assert bot.platform.sent == []


@pytest.mark.asyncio
async def test_authorized_admin_command_is_audited_and_replied_to(monkeypatch):
    from types import SimpleNamespace
    from stella.bot import StellaBot

    calls = []
    sent = []

    async def send(*args, **kwargs):
        sent.append((args, kwargs))

    async def admin_handler(room_id, uid, name, args, message):
        await bot._reply(room_id, "panel disponible", message)

    monkeypatch.setattr(
        "stella.bot._audit_admin_operation",
        lambda command, room_id, authorized: calls.append((command, room_id, authorized)),
    )

    bot = object.__new__(StellaBot)
    bot.settings = SimpleNamespace(command_prefix="!")
    bot.platform = SimpleNamespace(send_message=send)
    bot._is_admin = lambda uid: uid == "admin-1"
    bot._admin_panel = admin_handler

    message = {"messageId": "msg-1"}
    await bot._command("room-1", "admin-1", "Admin", "admin resumen", message)

    assert "_stella_admin_silent" not in message
    assert calls == [("admin", "room-1", True)]
    assert len(sent) == 1
    assert sent[0][0][1] == "panel disponible"


@pytest.mark.asyncio
async def test_unauthorized_admin_command_is_audited_and_silent(monkeypatch):
    from types import SimpleNamespace
    from stella.bot import StellaBot

    calls = []
    sent = []

    async def send(*args, **kwargs):
        sent.append((args, kwargs))

    async def admin_handler(room_id, uid, name, args, message):
        await bot._reply(room_id, "esto no debe aparecer", message)

    monkeypatch.setattr(
        "stella.bot._audit_admin_operation",
        lambda command, room_id, authorized: calls.append((command, room_id, authorized)),
    )

    bot = object.__new__(StellaBot)
    bot.settings = SimpleNamespace(command_prefix="!")
    bot.platform = SimpleNamespace(send_message=send)
    bot._is_admin = lambda uid: False
    bot._admin_panel = admin_handler

    message = {"messageId": "msg-2"}
    await bot._command("room-1", "member-1", "Member", "admin resumen", message)

    assert message["_stella_admin_silent"] is True
    assert calls == [("admin", "room-1", False)]
    assert sent == []
