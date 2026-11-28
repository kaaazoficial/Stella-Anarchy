from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from stella.ai import StellaAI
from stella.music_voice import (
    MusicVoice,
    TTS_VOICES,
    _allowed_audio_url,
    _audius_license_label,
    _license_label,
    _safe_filename,
)


def test_stella_models_use_configured_defaults(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test")
    ai = StellaAI()
    assert ai.gemini_model == "gemini-3.5-flash-lite"
    assert ai.groq_model == "openai/gpt-oss-20b"
    assert ai.primary == "gemini"


@pytest.mark.asyncio
async def test_groq_is_used_when_gemini_fails(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test")
    ai = StellaAI()

    async def fail_gemini(prompt, system):
        raise RuntimeError("Gemini unavailable")

    async def groq_answer(prompt, system):
        return "Respuesta de respaldo."

    ai._ask_gemini = fail_gemini
    ai._ask_groq = groq_answer
    assert await ai.ask("Hola") == "Respuesta de respaldo."
    assert ai.failures["gemini"] == 1
    await ai.close()


def test_music_sources_are_restricted_to_supported_https_hosts():
    assert _allowed_audio_url("https://archive.org/download/item/audio.mp3")
    assert _allowed_audio_url("https://ia800000.us.archive.org/file.mp3")
    assert _allowed_audio_url("https://upload.wikimedia.org/wikipedia/commons/a/audio.ogg")
    assert _allowed_audio_url("https://ccmixter.org/content/artist/song.mp3")
    assert _allowed_audio_url("https://api.audius.co/v1/tracks/123/stream?app_name=StellaAnarchy")
    assert _allowed_audio_url("https://discoveryprovider.audius.co/content/song.mp3")
    assert _allowed_audio_url("https://www.jamendo.com/track/123")
    assert not _allowed_audio_url("http://archive.org/download/item/audio.mp3")
    assert not _allowed_audio_url("https://youtube.com/watch?v=abc")
    assert not _allowed_audio_url("https://user:pass@archive.org/audio.mp3")
    assert not _allowed_audio_url("https://api.audius.co.evil.example/audio.mp3")
    assert not _allowed_audio_url("https://ccmixter.org.evil.example/audio.mp3")


def test_music_license_filter_requires_open_license():
    assert _license_label("https://creativecommons.org/licenses/by/4.0/") == "Creative Commons BY"
    assert _license_label("https://creativecommons.org/licenses/by-sa/4.0/") == "Creative Commons BY-SA"
    assert _license_label("https://creativecommons.org/publicdomain/zero/1.0/") == "CC0"
    assert _license_label("https://creativecommons.org/licenses/by-nc/4.0/") is None
    assert _license_label("") is None
    assert _audius_license_label("CC BY 4.0") == ("Creative Commons BY", "https://creativecommons.org/licenses/by/4.0/")
    assert _audius_license_label("CC BY-SA 4.0") == ("Creative Commons BY-SA", "https://creativecommons.org/licenses/by-sa/4.0/")
    assert _audius_license_label("CC0") == ("CC0", "https://creativecommons.org/publicdomain/zero/1.0/")
    assert _audius_license_label("CC BY-NC 4.0") is None
    assert _audius_license_label("All Rights Reserved") is None


def test_music_filename_validation_blocks_paths_and_unknown_types():
    assert _safe_filename("music.mp3") == "music.mp3"
    assert _safe_filename("../secret.mp3") is None
    assert _safe_filename("C:\\secret.mp3") is None
    assert _safe_filename("poster.jpg") is None


def test_spanish_voice_options_are_available():
    assert TTS_VOICES["catalina"] == "es-CL-CatalinaNeural"
    assert TTS_VOICES["carlos"] == "es-CO-GonzaloNeural"


@pytest.mark.asyncio
async def test_voice_room_refuses_to_join_without_an_active_call():
    class FakePlatform:
        async def request(self, method, path):
            assert method == "GET"
            assert path == "/live-rooms"
            return {"ok": True, "status": 200, "data": {"roomList": []}}

    voice = MusicVoice(FakePlatform())
    ok, message = await voice.ensure_room("thread-1")
    assert not ok
    assert "No hay una llamada de voz activa" in message
    await voice.close()

@pytest.mark.asyncio
async def test_audio_source_cleanup_closes_a_shared_source_once():
    class FakeSource:
        def __init__(self):
            self.closed = 0

        async def aclose(self):
            self.closed += 1

    voice = object.__new__(MusicVoice)
    source = FakeSource()
    state = {
        "audio_source": source,
        "music_source": source,
        "speech_source": source,
    }

    await voice._close_audio_sources(state)
    await voice._close_audio_sources(state)

    assert source.closed == 1


@pytest.mark.asyncio
async def test_audio_capture_splits_pcm_into_livekit_sized_frames():
    import numpy as np

    class Frame:
        def __init__(self, samples):
            self.data = bytearray(samples * 2)

    class FakeAudioFrame:
        sizes = []

        @classmethod
        def create(cls, sample_rate, channels, samples):
            assert sample_rate == 48000
            assert channels == 1
            cls.sizes.append(samples)
            return Frame(samples)

    class FakeSource:
        def __init__(self):
            self.captured = []

        async def capture_frame(self, frame):
            self.captured.append(len(frame.data) // 2)

    voice = object.__new__(MusicVoice)
    voice._rtc = type("RTC", (), {"AudioFrame": FakeAudioFrame})
    voice._np = np
    source = FakeSource()

    await voice._capture_pcm(source, None, np.arange(960, dtype=np.int16))

    assert FakeAudioFrame.sizes == [480, 480]
    assert source.captured == [480, 480]


@pytest.mark.asyncio
async def test_ensure_room_publishes_one_shared_audio_track_and_registers_events_first():
    class FakePlatform:
        def __init__(self):
            self.calls = []

        async def request(self, method, path):
            self.calls.append((method, path))
            if method == "GET":
                return {
                    "ok": True,
                    "status": 200,
                    "data": {"roomList": [{"threadId": "thread-1"}]},
                }
            if path.endswith("/join"):
                return {
                    "ok": True,
                    "status": 200,
                    "data": {"url": "wss://livekit.example", "token": "temporary"},
                }
            return {"ok": True, "status": 200, "data": {}}

    class FakeSource:
        def __init__(self, *args, **kwargs):
            self.closed = False
        async def aclose(self):
            self.closed = True

    class FakeTrack:
        @staticmethod
        def create_audio_track(name, source):
            return {"name": name, "source": source}

    class FakePublication:
        sid = "publication-1"

    class FakeParticipant:
        def __init__(self, room):
            self.room = room
            self.publications = []
        async def publish_track(self, track, options):
            assert self.room.connected
            assert self.room.ready_received
            self.publications.append((track, options))
            return FakePublication()

    class FakeRoom:
        def __init__(self):
            self.callbacks = {}
            self.connected = False
            self.disconnect_calls = 0
            self.ready_received = False
            self.local_participant = FakeParticipant(self)
        def on(self, event, callback):
            self.callbacks[event] = callback
        async def connect(self, url, token, options=None):
            assert set(("disconnected", "reconnecting", "reconnected", "connection_state_changed")).issubset(self.callbacks)
            assert url == "wss://livekit.example"
            assert token == "temporary"
            assert options.auto_subscribe is False
            assert options.connect_timeout == 20.0
            self.connected = True
            asyncio.get_running_loop().call_later(0.01, self.announce_ready)
        def announce_ready(self):
            self.ready_received = True
            voice.notify_room_ready("thread-1")
        def isconnected(self):
            return self.connected
        async def disconnect(self):
            self.disconnect_calls += 1
            self.connected = False

    room = FakeRoom()

    class FakeRTC:
        Room = lambda self: room
        AudioSource = FakeSource
        LocalAudioTrack = FakeTrack
        TrackSource = type("TrackSource", (), {"SOURCE_MICROPHONE": "microphone"})
        TrackPublishOptions = lambda self, **kwargs: kwargs
        RoomOptions = lambda self, **kwargs: type("RoomOptions", (), kwargs)()

    platform = FakePlatform()
    voice = MusicVoice(platform)
    voice._rtc = FakeRTC()
    ok, message = await voice.ensure_room("thread-1")

    assert ok
    state = voice.rooms["thread-1"]
    assert state["music_source"] is state["audio_source"]
    assert state["speech_source"] is not state["music_source"]
    assert len(room.local_participant.publications) == 2
    assert state["audio_publication_sid"] == "publication-1"
    assert state["speech_publication_sid"] == "publication-1"
    assert "canales independientes" in message

    await voice.leave_room("thread-1", request_leave=True)
    assert room.disconnect_calls == 1
    assert state["audio_source"].closed
    assert platform.calls.count(("POST", "/live-rooms/thread-1/leave")) == 1
    await voice.http.aclose()


@pytest.mark.asyncio
async def test_failed_music_track_is_not_requeued_forever():
    voice = object.__new__(MusicVoice)
    track = {
        "title": "broken track",
        "creator": "test",
        "source": "test",
        "license": "CC0",
        "url": "https://archive.org/audio.mp3",
    }
    state = {
        "connected": True,
        "music_queue": __import__("collections").deque([track]),
        "music_current": None,
        "music_skip": asyncio.Event(),
        "music_paused": asyncio.Event(),
        "music_volume": 0.75,
        "repeat_mode": "one",
        "music_source": object(),
    }
    state["music_paused"].set()
    voice.rooms = {"thread-1": state}
    sent = []

    async def fail_download(url):
        raise RuntimeError("source unavailable")

    async def capture_send(room_id, text, reply_id=None):
        sent.append(text)

    voice._download_audio = fail_download
    voice._send = capture_send

    await voice._music_worker("thread-1", state)

    assert not state["music_queue"]
    assert state["music_current"] is None
    assert len(sent) == 1
    assert "No pude reproducir" in sent[0]

@pytest.mark.asyncio
async def test_failed_audio_publication_disconnects_and_releases_source():
    class FakePlatform:
        def __init__(self):
            self.calls = []
        async def request(self, method, path):
            self.calls.append((method, path))
            if method == "GET":
                return {"ok": True, "status": 200, "data": {"roomList": [{"threadId": "thread-1"}]}}
            if path.endswith("/join"):
                return {"ok": True, "status": 200, "data": {"url": "wss://livekit.example", "token": "secret-token"}}
            return {"ok": True, "status": 200, "data": {}}

    class FakeSource:
        instances = []
        def __init__(self, *args, **kwargs):
            self.closed = False
            self.__class__.instances.append(self)
        async def aclose(self):
            self.closed = True

    class FakeTrack:
        @staticmethod
        def create_audio_track(name, source):
            return {"name": name, "source": source}

    class FakeParticipant:
        async def publish_track(self, track, options):
            raise RuntimeError("publication rejected")

    class FakeRoom:
        def __init__(self):
            self.callbacks = {}
            self.connected = False
            self.disconnect_calls = 0
            self.local_participant = FakeParticipant()
        def on(self, event, callback):
            self.callbacks[event] = callback
        async def connect(self, url, token, options=None):
            self.connected = True
            asyncio.get_running_loop().call_later(0.01, voice.notify_room_ready, "thread-1")
        def isconnected(self):
            return self.connected
        async def disconnect(self):
            self.disconnect_calls += 1
            self.connected = False

    room = FakeRoom()
    class FakeRTC:
        Room = lambda self: room
        AudioSource = FakeSource
        LocalAudioTrack = FakeTrack
        TrackSource = type("TrackSource", (), {"SOURCE_MICROPHONE": "microphone"})
        TrackPublishOptions = lambda self, **kwargs: kwargs
        RoomOptions = lambda self, **kwargs: type("RoomOptions", (), kwargs)()

    platform = FakePlatform()
    voice = MusicVoice(platform)
    voice._rtc = FakeRTC()

    ok, message = await voice.ensure_room("thread-1")

    assert not ok
    assert "publish_music" in message
    assert room.disconnect_calls == 1
    assert len(FakeSource.instances) == 2
    assert all(source.closed for source in FakeSource.instances)
    assert platform.calls.count(("POST", "/live-rooms/thread-1/leave")) == 1
    assert not voice.rooms
    await voice.http.aclose()


@pytest.mark.asyncio
async def test_music_waits_while_tts_owns_shared_audio_track():
    voice = object.__new__(MusicVoice)
    state = {
        "music_paused": asyncio.Event(),
        "speech_done": asyncio.Event(),
    }
    state["music_paused"].set()
    state["speech_done"].clear()
    task = asyncio.create_task(voice._wait_music_gate(state))
    await asyncio.sleep(0)
    assert not task.done()

    state["speech_done"].set()
    await asyncio.wait_for(task, timeout=0.2)



@pytest.mark.asyncio
async def test_room_ready_event_is_routed_to_livekit_waiter():
    from stella.bot import StellaBot

    received = []

    class FakeVoice:
        def notify_room_ready(self, room_id):
            received.append(room_id)

    bot = object.__new__(StellaBot)
    bot.voice = FakeVoice()
    await bot.handle_event({"type": "room.ready", "threadId": "thread-ready"})

    assert received == ["thread-ready"]


@pytest.mark.asyncio
async def test_archive_search_uses_fallback_terms_and_checks_license():
    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload
        @property
        def is_success(self):
            return True
        def raise_for_status(self):
            return None
        def json(self):
            return self.payload

    class FakeHTTP:
        def __init__(self):
            self.search_queries = []
        async def get(self, url, params=None, **kwargs):
            if "advancedsearch.php" in url:
                self.search_queries.append(params["q"])
                return FakeResponse({"response": {"docs": [{"identifier": "open-reggaeton"}]}})
            return FakeResponse({
                "metadata": {
                    "title": "Reggaeton instrumental",
                    "creator": "Independent artist",
                    "licenseurl": "https://creativecommons.org/licenses/by/4.0/",
                },
                "files": [{"name": "reggaeton.mp3", "size": "2048"}],
                "d1": "ia800000.us.archive.org",
                "dir": "/1/items/open-reggaeton",
            })

    voice = object.__new__(MusicVoice)
    voice.http = FakeHTTP()
    tracks = await voice.search_archive(
        "Reggaeton Latin urban high tempo bass drums synthesizers", limit=3
    )

    assert tracks and tracks[0]["license"] == "Creative Commons BY"
    assert "title:Reggaeton" in voice.http.search_queries[0]
    assert 'title:"Reggaeton Latin urban high tempo bass drums synthesizers"' not in voice.http.search_queries[0]
    assert tracks[0]["url"].startswith("https://ia800000.us.archive.org/")


@pytest.mark.asyncio
async def test_wikimedia_search_accepts_only_audio_with_open_license():
    class FakeResponse:
        def raise_for_status(self):
            return None
        def json(self):
            return {"query": {"pages": {"10": {
                "pageid": 10,
                "title": "File:Open instrumental.ogg",
                "imageinfo": [{
                    "url": "https://upload.wikimedia.org/wikipedia/commons/a/audio.ogg",
                    "descriptionurl": "https://commons.wikimedia.org/wiki/File:Open_instrumental.ogg",
                    "mime": "audio/ogg",
                    "mediatype": "AUDIO",
                    "size": 4096,
                    "extmetadata": {
                        "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/4.0/"},
                        "LicenseShortName": {"value": "CC BY 4.0"},
                        "Artist": {"value": "Independent artist"},
                        "ObjectName": {"value": "Open instrumental"},
                    },
                }],
            }}}}

    class FakeHTTP:
        async def get(self, url, **kwargs):
            assert url == "https://commons.wikimedia.org/w/api.php"
            return FakeResponse()

    voice = object.__new__(MusicVoice)
    voice.http = FakeHTTP()
    tracks = await voice.search_wikimedia("instrumental study")

    assert len(tracks) == 1
    assert tracks[0]["source"] == "Wikimedia Commons"
    assert tracks[0]["license"] == "Creative Commons BY"


@pytest.mark.asyncio
async def test_dj_search_uses_available_catalogs_without_api_keys(monkeypatch):
    monkeypatch.setenv("MUSIC_AI_SEARCH", "0")
    monkeypatch.delenv("JAMENDO_CLIENT_ID", raising=False)
    voice = object.__new__(MusicVoice)
    voice.search_results = {}
    calls = []

    async def archive(query, limit):
        calls.append(("archive", query, limit))
        return [{"url": "https://archive.org/a.mp3", "identifier": "a", "title": "A", "creator": "Artist", "license": "CC0", "source": "Archive"}]

    async def commons(query, limit):
        calls.append(("commons", query, limit))
        return [{"url": "https://upload.wikimedia.org/audio.ogg", "identifier": "b", "title": "B", "creator": "Artist", "license": "CC BY", "source": "Commons"}]

    async def openverse(query, limit):
        calls.append(("openverse", query, limit))
        return []

    async def ccmixter(query, limit):
        calls.append(("ccmixter", query, limit))
        return []

    async def audius(query, limit):
        calls.append(("audius", query, limit))
        return []

    voice.search_archive = archive
    voice.search_wikimedia = commons
    voice.search_openverse = openverse
    voice.search_ccmixter = ccmixter
    voice.search_audius = audius

    ok, message = await voice.search_music("thread-1", "instrumental study")

    assert ok
    assert {item[0] for item in calls} == {"archive", "commons", "openverse", "ccmixter", "audius"}
    assert "Archive" in message and "Commons" in message
    assert len(voice.search_results["thread-1"]["items"]) == 2






@pytest.mark.asyncio
async def test_ccmixter_search_checks_license_and_direct_audio_file():
    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload
        def raise_for_status(self):
            return None
        def json(self):
            return self.payload

    class FakeHTTP:
        def __init__(self):
            self.calls = []
        async def get(self, url, params=None, **kwargs):
            self.calls.append((url, params or {}))
            params = params or {}
            if params.get("dataview") == "info":
                return FakeResponse([{
                    "file_name": "Open beat",
                    "user_name": "artist",
                    "user_real_name": "Independent Artist",
                    "license_name": "Attribution",
                    "license_url": "https://creativecommons.org/licenses/by/4.0/",
                    "file_page_url": "https://ccmixter.org/files/artist/123",
                }])
            if params.get("dataview") == "files":
                return FakeResponse([{
                    "download_url": "https://ccmixter.org/content/artist/open-beat.mp3",
                    "file_name": "open-beat.mp3",
                    "file_rawsize": "4096",
                }])
            if params.get("lic") == "by":
                return FakeResponse([{"upload_id": "123", "user_name": "artist"}])
            return FakeResponse([])

    voice = object.__new__(MusicVoice)
    voice.http = FakeHTTP()
    tracks = await voice.search_ccmixter("instrumental study", limit=3)

    assert len(tracks) == 1
    assert tracks[0]["source"] == "ccMixter"
    assert tracks[0]["license"] == "Creative Commons BY"
    assert tracks[0]["creator"] == "Independent Artist"
    assert tracks[0]["url"].endswith(".mp3")
    assert any(call[1].get("lic") == "by" for call in voice.http.calls)
    assert any(call[1].get("dataview") == "files" for call in voice.http.calls)


@pytest.mark.asyncio
async def test_audius_search_uses_stream_endpoint_and_rejects_restricted_tracks():
    class FakeResponse:
        def raise_for_status(self):
            return None
        def json(self):
            return {"data": [
                {
                    "id": "track-1",
                    "title": "Open Track",
                    "license": "CC BY-SA 4.0",
                    "is_streamable": True,
                    "duration": 180,
                    "permalink": "https://audius.co/artist/open-track",
                    "user": {"name": "Independent Artist"},
                },
                {
                    "id": "track-2",
                    "title": "All Rights Reserved",
                    "license": "All Rights Reserved",
                    "is_streamable": True,
                    "duration": 180,
                },
                {
                    "id": "track-3",
                    "title": "Non-commercial",
                    "license": "CC BY-NC 4.0",
                    "is_streamable": True,
                    "duration": 180,
                },
            ]}

    class FakeHTTP:
        def __init__(self):
            self.calls = []
        async def get(self, url, params=None, **kwargs):
            self.calls.append((url, params, kwargs))
            return FakeResponse()

    voice = object.__new__(MusicVoice)
    voice.http = FakeHTTP()
    tracks = await voice.search_audius("ambient", limit=5)

    assert len(tracks) == 1
    assert tracks[0]["license"] == "Creative Commons BY-SA"
    assert tracks[0]["creator"] == "Independent Artist"
    assert tracks[0]["source"] == "Audius"
    assert "/tracks/track-1/stream" in tracks[0]["url"]
    assert "https://" in tracks[0]["item_url"]
    assert voice.http.calls[0][0] == "https://api.audius.co/v1/tracks/search"


@pytest.mark.asyncio
async def test_music_search_page_uses_native_buttons_and_never_posts_source_urls():
    class FakePlatform:
        def __init__(self):
            self.sent = []
        async def send_message(self, room_id, content, **kwargs):
            self.sent.append((room_id, content, kwargs))
            return "msg-1"

    voice = object.__new__(MusicVoice)
    voice.platform = FakePlatform()
    voice.search_results = {
        "room": {
            "query": "lofi",
            "expires": __import__("time").monotonic() + 600,
            "items": [
                {
                    "title": f"Track {i}",
                    "creator": "Artist",
                    "source": "Wikimedia Commons",
                    "license": "CC BY",
                    "url": f"https://upload.wikimedia.org/audio{i}.ogg",
                    "item_url": f"https://commons.wikimedia.org/item{i}",
                }
                for i in range(1, 7)
            ],
        }
    }
    voice.interaction_sessions = {}

    assert await voice.send_search_page("room", 1)
    _, content, options = voice.platform.sent[0]
    assert len(options["buttons"]) == 5
    assert options["buttons"][-1] == {"id": "page_2", "label": "Siguientes pistas →"}
    assert "https://" not in content
    assert ("room", "msg-1") in voice.interaction_sessions


@pytest.mark.asyncio
async def test_music_button_interaction_removes_buttons_and_queues_selected_track():
    class FakePlatform:
        def __init__(self):
            self.edits = []
            self.sent = []
        async def edit_message(self, room_id, message_id, *, remove_buttons=True):
            self.edits.append((room_id, message_id, remove_buttons))
            return True
        async def send_message(self, room_id, content, **kwargs):
            self.sent.append((room_id, content, kwargs))
            return True

    voice = object.__new__(MusicVoice)
    voice.platform = FakePlatform()
    voice.interaction_sessions = {
        ("room", "msg-1"): {
            "items": [{
                "title": "Track 1",
                "creator": "Artist",
                "source": "Archive",
                "license": "CC0",
                "url": "https://archive.org/audio.mp3",
            }],
            "expires": __import__("time").monotonic() + 600,
        }
    }
    queued = []

    async def queue_track(room_id, track, *, enqueue):
        queued.append((room_id, track["title"], enqueue))
        return True, "Reproduciendo Track 1."

    voice._queue_track = queue_track

    assert await voice.handle_interaction({
        "type": "interaction",
        "threadId": "room",
        "messageId": "msg-1",
        "buttonId": "play_1",
        "user": {"uid": "user-1"},
    })
    assert queued == [("room", "Track 1", False)]
    assert voice.platform.edits == [("room", "msg-1", True)]
    assert "https://" not in voice.platform.sent[0][1]
    assert ("room", "msg-1") not in voice.interaction_sessions


@pytest.mark.asyncio
async def test_music_button_navigation_invalidates_old_buttons_and_sends_next_page():
    class FakePlatform:
        def __init__(self):
            self.edits = []
            self.sent = []
        async def edit_message(self, room_id, message_id, *, remove_buttons=True):
            self.edits.append((room_id, message_id))
            return True
        async def send_message(self, room_id, content, **kwargs):
            self.sent.append((room_id, content, kwargs))
            return "msg-2"

    voice = object.__new__(MusicVoice)
    voice.platform = FakePlatform()
    voice.interaction_sessions = {
        ("room", "msg-1"): {
            "items": [],
            "expires": __import__("time").monotonic() + 600,
        }
    }
    voice.search_results = {
        "room": {
            "query": "lofi",
            "expires": __import__("time").monotonic() + 600,
            "items": [
                {"title": f"Track {i}", "creator": "Artist", "source": "Archive", "license": "CC0"}
                for i in range(1, 7)
            ],
        }
    }

    assert await voice.handle_interaction({
        "type": "interaction",
        "threadId": "room",
        "messageId": "msg-1",
        "buttonId": "page_2",
    })
    assert voice.platform.edits == [("room", "msg-1")]
    assert voice.platform.sent[0][2]["buttons"][0]["id"] == "play_5"
    assert ("room", "msg-2") in voice.interaction_sessions

@pytest.mark.asyncio
async def test_concurrent_leave_requests_call_anarchy_once():
    class FakePlatform:
        def __init__(self):
            self.leave_calls = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()
        async def request(self, method, path):
            if method == "POST" and path.endswith("/leave"):
                self.leave_calls += 1
                self.started.set()
                await self.release.wait()
                return {"ok": True, "status": 200, "data": {}}
            return {"ok": True, "status": 200, "data": {}}

    platform = FakePlatform()
    voice = MusicVoice(platform)
    state = {
        "connected": True,
        "room": None,
        "music_task": None,
        "speech_task": None,
        "audio_source": None,
    }
    voice.rooms["thread-1"] = state

    first = asyncio.create_task(voice.leave_room("thread-1", request_leave=True))
    await platform.started.wait()
    second = asyncio.create_task(voice.leave_room("thread-1", request_leave=True))
    await asyncio.sleep(0)
    platform.release.set()
    await asyncio.gather(first, second)

    assert platform.leave_calls == 1
    await voice.http.aclose()




@pytest.mark.asyncio
async def test_interaction_event_is_routed_to_music_voice_handler():
    from stella.bot import StellaBot

    received = []

    class FakeVoice:
        async def handle_interaction(self, event):
            received.append(event["buttonId"])
            return True

    class FakeSharedState:
        async def get_setting(self, key):
            return None

    bot = object.__new__(StellaBot)
    bot.voice = FakeVoice()
    bot.shared_state = FakeSharedState()
    await bot.handle_event({
        "type": "interaction",
        "threadId": "thread-1",
        "messageId": "message-1",
        "buttonId": "play_1",
    })

    assert received == ["play_1"]


def test_youtube_music_search_creates_only_official_youtube_links():
    voice = object.__new__(MusicVoice)
    message = voice.youtube_search_message("Daft Punk One More Time")

    assert "https://www.youtube.com/results?search_query=Daft%20Punk%20One%20More%20Time" in message
    assert "https://www.youtube.com/t/terms" in message
    assert "no descarga" in message.casefold()
    assert "LiveKit" in message


def test_youtube_music_accepts_official_video_links_without_proxying_media():
    voice = object.__new__(MusicVoice)
    message = voice.youtube_search_message("https://youtu.be/dQw4w9WgXcQ")

    assert "https://www.youtube.com/watch?v=dQw4w9WgXcQ" in message
    assert "results?search_query=" not in message
    assert "LiveKit" in message


@pytest.mark.asyncio
async def test_dj_youtube_action_returns_external_search_not_livekit_audio():
    voice = object.__new__(MusicVoice)
    message = await voice._dj_command("room-1", "youtube lofi chill")

    assert "https://www.youtube.com/results?search_query=lofi%20chill" in message
    assert "retransmite" in message



@pytest.mark.asyncio
async def test_livekit_retries_one_transient_track_publication():
    voice = object.__new__(MusicVoice)
    voice.rooms = {}
    state = {"connected": True, "connection_ready": asyncio.Event()}
    state["connection_ready"].set()
    room = SimpleNamespace(isconnected=lambda: True)
    attempts = []

    class Participant:
        async def publish_track(self, track, options):
            attempts.append(track)
            if len(attempts) == 1:
                raise RuntimeError("temporary LiveKit publish transport error")
            return SimpleNamespace(sid="published")

    room.local_participant = Participant()
    voice.rooms["room"] = state

    publication = await voice._publish_track_with_retry(
        "room", room, state, "track", {}, stage="música"
    )

    assert publication.sid == "published"
    assert attempts == ["track"] * 2


@pytest.mark.asyncio
async def test_music_worker_retries_one_transient_playback_interruption():
    from collections import deque

    voice = object.__new__(MusicVoice)
    track = {
        "title": "Open track",
        "creator": "Artist",
        "source": "Archive",
        "license": "CC0",
        "url": "https://archive.org/audio.mp3",
    }
    state = {
        "connected": True,
        "music_queue": deque([track]),
        "music_current": None,
        "music_skip": asyncio.Event(),
        "music_paused": asyncio.Event(),
        "music_volume": 0.75,
        "repeat_mode": "off",
        "music_source": object(),
    }
    state["music_paused"].set()
    voice.rooms = {"room": state}
    messages = []
    playback_attempts = []

    async def download(url):
        return b"audio"

    async def play(*args, **kwargs):
        playback_attempts.append(1)
        if len(playback_attempts) == 1:
            raise RuntimeError("LiveKit connection temporarily closed")

    async def send(room_id, content, reply_id=None):
        messages.append(content)

    voice._download_audio = download
    voice._play_audio_bytes = play
    voice._send = send

    await voice._music_worker("room", state)

    assert len(playback_attempts) == 2
    assert state["music_current"] is None
    assert not state["music_queue"]
    assert any("reintentando" in message.casefold() for message in messages)
    assert not any("No pude reproducir" in message for message in messages)



@pytest.mark.asyncio
async def test_livekit_does_not_retry_permission_errors():
    voice = object.__new__(MusicVoice)
    assert not voice._is_transient_livekit_error(RuntimeError("LiveKit HTTP 403 permission denied"))
    assert not voice._is_transient_livekit_error(RuntimeError("Unauthorized: forbidden"))



@pytest.mark.asyncio
async def test_dj_help_shows_available_controls():
    voice = object.__new__(MusicVoice)
    response = await voice._dj_command("room", "ayuda")
    assert "!dj buscar <tema>" in response
    assert "!dj detener" in response


@pytest.mark.asyncio
async def test_dj_search_requires_a_query():
    voice = object.__new__(MusicVoice)
    response = await voice._dj_command("room", "buscar")
    assert "Indica qué quieres escuchar" in response


@pytest.mark.asyncio
async def test_stop_music_clears_queue_and_joins_playback_worker():
    from collections import deque

    voice = object.__new__(MusicVoice)
    skip_event = asyncio.Event()
    paused_event = asyncio.Event()
    paused_event.set()

    class FakeSource:
        def __init__(self):
            self.cleared = 0
        def clear_queue(self):
            self.cleared += 1

    source = FakeSource()
    track = {"title": "track"}
    state = {
        "music_queue": deque([track]),
        "music_skip": skip_event,
        "music_paused": paused_event,
        "music_source": source,
        "music_current": track,
    }

    async def worker():
        await skip_event.wait()

    state["music_task"] = asyncio.create_task(worker())
    voice.rooms = {"room-1": state}
    await asyncio.sleep(0)

    message = await voice.stop_music("room-1")

    assert not state["music_queue"]
    assert source.cleared == 1
    assert state["music_task"].done()
    assert state["music_current"] is None
    assert "Música detenida" in message


def test_voice_diagnostics_are_aggregate_and_exclude_room_identifiers():
    from collections import deque

    voice = object.__new__(MusicVoice)
    voice._rtc = object()
    voice._av = object()
    voice._np = object()
    voice._edge_tts = None
    voice._load_errors = {"tts": "ModuleNotFoundError"}

    class ConnectedRoom:
        @staticmethod
        def isconnected():
            return True

    state = {
        "room": ConnectedRoom(),
        "connected": True,
        "connection_state": "connected",
        "music_current": {"title": "private track"},
        "music_queue": deque([{"title": "other track"}]),
        "speech_queue": asyncio.Queue(),
        "tts_enabled": True,
    }
    state["speech_queue"].put_nowait("private speech")
    voice.rooms = {"private-room-id": state}

    report = voice.diagnostics

    assert report["active_rooms"] == 1
    assert report["connected_rooms"] == 1
    assert report["music_players"] == 1
    assert report["queued_tracks"] == 1
    assert report["speech_queues"] == 1
    assert report["tts_enabled_rooms"] == 1
    assert "private-room-id" not in repr(report)
    assert "private track" not in repr(report)
    assert "private speech" not in repr(report)


@pytest.mark.asyncio
async def test_disabling_room_reading_cancels_active_tts_and_releases_music_gate():
    voice = object.__new__(MusicVoice)
    queue = asyncio.Queue()
    await queue.put("pendiente")
    speech_done = asyncio.Event()
    speech_done.clear()
    tts_active = asyncio.Event()
    tts_active.set()
    started = asyncio.Event()

    async def active_worker():
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(active_worker())
    await started.wait()
    state = {
        "tts_enabled": True,
        "speech_queue": queue,
        "speech_task": task,
        "speech_done": speech_done,
        "tts_active": tts_active,
    }
    voice.rooms = {"room-1": state}

    ok, message = await voice.enable_reading("room-1", False)

    assert ok is True
    assert "desactivada" in message
    assert task.cancelled()
    assert state["speech_task"] is None
    assert queue.empty()
    assert speech_done.is_set()
    assert not tts_active.is_set()
