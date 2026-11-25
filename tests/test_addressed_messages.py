import pytest
from types import SimpleNamespace
import asyncio

from stella.bot import StellaBot

class Platform:
    def __init__(self):
        self.identity = {"uid":"stella-account","botId":"stella-bot"}
        self.sent = []
        self.ids = {"stella-msg"}
    def has_sent_message(self, room, message_id):
        return message_id in self.ids
    async def send_message(self, room, content, reply_id=None):
        self.sent.append((room, content, reply_id))
        return True

class Store:
    def __init__(self):
        self.settings = {}
    def touch_user(self,*a): pass
    def record_activity(self,*a): pass
    def add_warning(self,*a): return 1
    def get_setting(self, room_id, key, default):
        return self.settings.get((str(room_id), str(key)), default)
    def set_setting(self, room_id, key, value):
        self.settings[(str(room_id), str(key))] = str(value)
    def auto_reply_rules(self,*a): return []
    def get_profile(self,*a): return {"uid":a[0] if a else "u"}

class Voice:
    def __init__(self):
        self.speech=[]
        self.enabled=False
        self.speech_stopped=False
    async def enqueue_speech(self,*a):
        if self.enabled: self.speech.append(a)
    async def stop_all_speech(self):
        self.speech_stopped=True
    async def leave_room(self,*a,**k): pass

def bot():
    value=object.__new__(StellaBot)
    value.settings=SimpleNamespace(command_prefix="!", sinner_uid="owner", admin_uids="")
    value.platform=Platform()
    value.store=Store()
    value.voice=Voice()
    value._check_antispam=lambda *a,**k:(False,None)
    return value

def test_plain_chat_does_not_receive_a_chat_reply():
    b=bot()
    asyncio.run(b.handle_event({"type":"message","threadId":"r","message":{"content":"hola a todos","author":{"uid":"u","nickname":"Persona"}}}))
    assert b.platform.sent == []

def test_affection_without_address_does_not_trigger_stella():
    b=bot()
    asyncio.run(b.handle_event({"type":"message","threadId":"r","message":{"content":"te quiero","author":{"uid":"u","nickname":"Persona"}}}))
    assert b.platform.sent == []

@pytest.mark.parametrize("content", [
    "Stella", "@Stella", "Stella?", "Stella.", "Stella!", "oye Stella",
    "Hey, Stella", "Hola Stella", "Stella, hola", "Stella ¿?",
])
def test_name_only_and_short_greeting_calls_get_a_natural_presence_reply(content):
    b=bot()
    calls=[]
    async def capture_ai(*args):
        calls.append(args)
    b._ai_assist=capture_ai

    asyncio.run(b.handle_event({
        "type":"message",
        "threadId":"r",
        "message":{"content":content,"author":{"uid":"u","nickname":"Persona"}},
    }))

    assert len(b.platform.sent)==1
    assert "Aquí estoy" in b.platform.sent[0][1]
    assert calls == []

def test_reply_to_stella_message_id_is_recognized():
    b=bot()
    assert b._is_stella_reply("r",{"replyMessageId":"stella-msg"})

def test_reply_to_non_stella_message_is_not_recognized():
    b=bot()
    assert not b._is_stella_reply("r",{"replyMessage":{"messageId":"other","author":{"uid":"other-user"}}})

def test_structured_stella_mention_is_recognized():
    b=bot()
    assert b._is_stella_mentioned({"mentions":[{"uid":"stella-account"}]})
    assert not b._is_stella_mentioned({"mentions":[{"nickname":"other bot"}]})



def test_common_prefix_commands_work_without_name_or_reply():
    for command in ("!ayuda", "!menu", "!ping"):
        b=bot()
        calls=[]
        async def capture(*args):
            calls.append(args)
        b._command=capture
        asyncio.run(b.handle_event({
            "type":"message",
            "threadId":"r",
            "message":{"content":command,"author":{"uid":"u","nickname":"Persona"}},
        }))
        assert len(calls) == 1
        assert calls[0][3] == command[1:]
        assert b.platform.sent == []


def test_calling_stella_by_name_allows_prefixed_command():
    for content in ("Stella !menu", "@Stella !ayuda", "oye Stella -ping"):
        b=bot()
        calls=[]
        async def capture(*args):
            calls.append(args)
        b._command=capture
        asyncio.run(b.handle_event({
            "type":"message",
            "threadId":"r",
            "message":{"content":content,"author":{"uid":"u","nickname":"Persona"}},
        }))
        assert len(calls) == 1
        assert calls[0][3] in {"menu", "ayuda", "ping"}


def test_reply_to_stella_allows_prefixed_command_without_name():
    b=bot()
    calls=[]
    async def capture(*args):
        calls.append(args)
    b._command=capture
    asyncio.run(b.handle_event({"type":"message","threadId":"r","message":{"content":"!menu","replyMessageId":"stella-msg","author":{"uid":"u","nickname":"Persona"}}}))
    assert len(calls) == 1
    assert calls[0][3] == "menu"

def test_help_command_sends_all_six_pages():
    b=bot()
    asyncio.run(b._help_command("r", "u", "Persona", "", {"messageId":"request"}))
    assert len(b.platform.sent) == 6
    expected_titles = (
        "1. AYUDA E INTELIGENCIA ARTIFICIAL",
        "2. SOCIAL Y PERFILES",
        "3. JUEGOS, ECONOMÍA Y COMUNIDAD",
        "4. MÚSICA, VOZ Y LECTURA",
        "5. UTILIDADES Y MULTIMEDIA",
        "6. ADMINISTRACIÓN Y ESTADO",
    )
    delivered = "\\n".join(message for _, message, _ in b.platform.sent)
    for title in expected_titles:
        assert title in delivered

def test_unknown_command_suggests_typo_without_executing_it():
    b=bot()
    handled=[]
    async def capture_help(*args):
        handled.append(args)
    b._help_command=capture_help

    asyncio.run(b._command("r", "u", "Persona", "ayudaa", {}))

    assert handled == []
    assert len(b.platform.sent) == 1
    assert "¿Quisiste decir !ayuda?" in b.platform.sent[0][1]
    assert "No ejecutaré la sugerencia automáticamente." in b.platform.sent[0][1]


def test_autotest_command_suggestion_check_passes():
    b=bot()
    b._is_admin=lambda uid: True

    asyncio.run(b._autotest("r", "u", "Admin", "", {}))

    assert len(b.platform.sent) == 1
    assert "5/5 correctas" in b.platform.sent[0][1]
    assert "✓ sugerencia de comandos" in b.platform.sent[0][1]
    assert "✓ silencio global" in b.platform.sent[0][1]

def _message(content, uid="u"):
    return {
        "type":"message",
        "threadId":"room-a",
        "message":{"content":content,"author":{"uid":uid,"nickname":uid}},
    }


def test_owner_can_enable_global_silence_without_mentioning_stella():
    b=bot()
    asyncio.run(b.handle_event(_message("No le hables a nadie", "owner")))

    assert b.store.get_setting("__stella_global__", "chat_silence", "0") == "1"
    assert b.voice.speech_stopped is True
    assert len(b.platform.sent) == 1
    assert "silencio global activado" in b.platform.sent[0][1]


def test_owner_can_enable_global_silence_with_direct_natural_call():
    b=bot()
    asyncio.run(b.handle_event(_message("Stella, no le hables a nadie", "owner")))

    assert b.store.get_setting("__stella_global__", "chat_silence", "0") == "1"
    assert len(b.platform.sent) == 1


def test_non_owner_cannot_enable_global_silence():
    b=bot()
    asyncio.run(b.handle_event(_message("Stella, no le hables a nadie", "moderator")))

    assert b.store.get_setting("__stella_global__", "chat_silence", "0") == "0"
    assert len(b.platform.sent) == 1
    assert "Solo el propietario de Stella puede activar" in b.platform.sent[0][1]


def test_global_silence_blocks_commands_chat_replies_and_voice():
    b=bot()
    b.voice.enabled=True
    b.store.set_setting("__stella_global__", "chat_silence", "1")

    asyncio.run(b.handle_event(_message("!ping", "u")))
    asyncio.run(b.handle_event(_message("Stella, hola", "u")))
    asyncio.run(b.handle_event(_message("mensaje normal", "u")))

    assert b.platform.sent == []
    assert b.voice.speech == []


def test_owner_can_restore_chat_with_natural_phrase_while_silenced():
    b=bot()
    b.store.set_setting("__stella_global__", "chat_silence", "1")

    asyncio.run(b.handle_event(_message("Ya puedes volver a hablar", "owner")))

    assert b.store.get_setting("__stella_global__", "chat_silence", "1") == "0"
    assert len(b.platform.sent) == 1
    assert "silencio global desactivado" in b.platform.sent[0][1]


def test_owner_can_restore_chat_with_reversible_command_while_silenced():
    b=bot()
    b.store.set_setting("__stella_global__", "chat_silence", "1")

    asyncio.run(b.handle_event(_message("!hablarchat", "owner")))

    assert b.store.get_setting("__stella_global__", "chat_silence", "1") == "0"
    assert len(b.platform.sent) == 1


def test_owner_can_toggle_silence_with_commands():
    b=bot()

    asyncio.run(b.handle_event(_message("!silenciarchat", "owner")))
    assert b.store.get_setting("__stella_global__", "chat_silence", "0") == "1"

    asyncio.run(b.handle_event(_message("!despertar", "owner")))
    assert b.store.get_setting("__stella_global__", "chat_silence", "1") == "0"


def test_autotest_silence_phrase_recognition():
    b=bot()
    assert b._is_silence_request("No le hables a nadie")
    assert b._is_silence_request("Stella, no le hables a nadie")
    assert b._is_silence_request("por favor no le hables a nadie")
    assert b._is_resume_request("Ya puedes volver a hablar")



def test_voice_reading_queue_is_cleared_and_worker_cancelled_on_global_silence():
    from stella.music_voice import MusicVoice

    async def scenario():
        voice = object.__new__(MusicVoice)
        queue = asyncio.Queue()
        queue.put_nowait("pending speech")
        task = asyncio.create_task(asyncio.sleep(30))
        state = {"speech_queue":queue, "speech_task":task}
        voice.rooms = {"room-a":state}

        await voice.stop_all_speech()

        assert queue.empty()
        assert task.cancelled()
        assert state["speech_task"] is None

    asyncio.run(scenario())


@pytest.mark.asyncio
async def test_reply_strips_raw_urls_from_all_command_responses():
    from stella.bot import StellaBot

    sent = []

    class FakePlatform:
        async def send_message(self, room_id, content, *, reply_id=None):
            sent.append((room_id, content, reply_id))
            return True

    bot = object.__new__(StellaBot)
    bot.platform = FakePlatform()
    await bot._reply(
        "room-1",
        "Fuente: Wikimedia Commons https://commons.wikimedia.org/wiki/File:Music.ogg",
        {"messageId": "msg-1"},
    )

    assert len(sent) == 1
    assert "https://" not in sent[0][1]
    assert "[enlace omitido]" in sent[0][1]
    assert "Wikimedia Commons" in sent[0][1]
    assert sent[0][2] == "msg-1"



def test_name_only_vocative_removes_question_and_exclamation_punctuation():
    from stella.identity import extract_stella_call

    for content in ("Stella?", "Stella.", "Stella!", "@Stella?", "oye Stella..."):
        assert extract_stella_call(content) == ""


def test_real_question_after_name_is_preserved():
    from stella.identity import extract_stella_call

    assert extract_stella_call("Stella, ¿cómo estás?") == "¿cómo estás?"
    assert extract_stella_call("Oye Stella, explícame esto") == "explícame esto"
