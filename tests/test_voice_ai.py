from __future__ import annotations

import asyncio

import pytest

from stella.ai import StellaAI
from stella.music_voice import (
    MusicVoice,
    TTS_VOICES,
    _allowed_audio_url,
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
    assert _allowed_audio_url("https://www.jamendo.com/track/123")
    assert not _allowed_audio_url("http://archive.org/download/item/audio.mp3")
    assert not _allowed_audio_url("https://youtube.com/watch?v=abc")
    assert not _allowed_audio_url("https://user:pass@archive.org/audio.mp3")


def test_music_license_filter_requires_open_license():
    assert _license_label("https://creativecommons.org/licenses/by/4.0/") == "Creative Commons BY"
    assert _license_label("https://creativecommons.org/licenses/by-sa/4.0/") == "Creative Commons BY-SA"
    assert _license_label("https://creativecommons.org/publicdomain/zero/1.0/") == "CC0"
    assert _license_label("https://creativecommons.org/licenses/by-nc/4.0/") is None
    assert _license_label("") is None


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
            self.publications.append((track, options))
            return FakePublication()

    class FakeRoom:
        def __init__(self):
            self.callbacks = {}
            self.connected = False
            self.disconnect_calls = 0
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
    assert state["music_source"] is state["speech_source"] is state["audio_source"]
    assert len(room.local_participant.publications) == 1
    assert state["audio_publication_sid"] == "publication-1"
    assert "canal de audio" in message

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
    assert "publish_audio" in message
    assert room.disconnect_calls == 1
    assert len(FakeSource.instances) == 1
    assert FakeSource.instances[0].closed
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

