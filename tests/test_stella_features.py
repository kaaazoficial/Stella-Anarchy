from __future__ import annotations

import time
from pathlib import Path

from stella.ai import StellaAI
from stella.help import build_commands_help_text, filter_commands_help_text
from stella.identity import extract_stella_call
from stella.research import classify_research_intent
from stella.storage import Store


def test_stella_help_catalog_has_general_categories_only():
    full = build_commands_help_text("!")
    assert "STELLA" in full
    assert "!perfil" in full
    assert "!diario" in full
    assert "!dj buscar" in full
    assert "genshin" not in full.casefold()
    assert "teyvat" not in full.casefold()
    assert "katheryne" not in full.casefold()
    social = filter_commands_help_text(full, "social")
    assert "SOCIAL Y PERFILES" in social
    assert "ECONOMÍA" not in social


def test_stella_identity_recognizes_direct_calls_without_casual_mentions():
    assert extract_stella_call("Oye Stella, ¿puedes ayudarme?") == "¿puedes ayudarme?"
    assert extract_stella_call("Stella revisa este texto") == "revisa este texto"
    assert extract_stella_call("Hablé con Stella ayer") is None


def test_research_intent_routes_general_topics_without_game_domain():
    music = classify_research_intent("Investiga discografía y canciones de una banda")
    science = classify_research_intent("Investiga un estudio científico sobre astronomía")
    assert music["music"]
    assert science["academic"]
    assert science["nasa"]
    assert "genshin" not in music
    assert "genshin" not in science


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
