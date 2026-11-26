import pytest
from types import SimpleNamespace
import asyncio

from stella.bot import StellaBot

class Platform:
    def __init__(self):
        self.identity = {"uid":"stella-account","botId":"stella-bot"}
        self.sent = []
        self.read_messages = {}
        self.read_requests = []
        self.ids = {"stella-msg"}
    def has_sent_message(self, room, message_id):
        return message_id in self.ids
    async def send_message(self, room, content, reply_id=None):
        self.sent.append((room, content, reply_id))
        return True
    async def get_message(self, room, message_id):
        self.read_requests.append((room, message_id))
        return self.read_messages.get(str(message_id))

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
    def add_affinity(self,*a): return 1

class Voice:
    def __init__(self):
        self.speech=[]
        self.enabled=False
        self.speech_stopped=False
        self.interactions=[]
    async def enqueue_speech(self,*a):
        if self.enabled: self.speech.append(a)
    async def stop_all_speech(self):
        self.speech_stopped=True
    async def handle_interaction(self,event):
        self.interactions.append(event)
    async def leave_room(self,*a,**k): pass

class SharedState:
    def __init__(self):
        self.values = {}

    async def get_setting(self, key):
        return self.values.get(key)

    async def set_setting(self, key, value):
        self.values[key] = str(value)

    async def close(self):
        return None


def bot():
    value=object.__new__(StellaBot)
    value.settings=SimpleNamespace(command_prefix="!", sinner_uid="owner", admin_uids="")
    value.platform=Platform()
    value.store=Store()
    value.voice=Voice()
    value.shared_state=SharedState()
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

    assert b.shared_state.values["chat_silence"] == "1"
    assert b.voice.speech_stopped is True
    assert len(b.platform.sent) == 1
    assert "silencio global activado" in b.platform.sent[0][1]


def test_owner_can_enable_global_silence_with_direct_natural_call():
    b=bot()
    asyncio.run(b.handle_event(_message("Stella, no le hables a nadie", "owner")))

    assert b.shared_state.values["chat_silence"] == "1"
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
    b.shared_state.values["chat_silence"] = "1"
    b.store.set_setting("__stella_global__", "chat_silence", "1")

    asyncio.run(b.handle_event(_message("!ping", "u")))
    asyncio.run(b.handle_event(_message("Stella, hola", "u")))
    asyncio.run(b.handle_event(_message("mensaje normal", "u")))

    assert b.platform.sent == []
    assert b.voice.speech == []
    assert b.voice.speech_stopped is True


def test_owner_can_restore_chat_with_natural_phrase_while_silenced():
    b=bot()
    b.shared_state.values["chat_silence"] = "1"

    asyncio.run(b.handle_event(_message("Ya puedes volver a hablar", "owner")))

    assert b.shared_state.values["chat_silence"] == "0"
    assert len(b.platform.sent) == 1
    assert "silencio global desactivado" in b.platform.sent[0][1]


def test_owner_can_restore_chat_with_reversible_command_while_silenced():
    b=bot()
    b.shared_state.values["chat_silence"] = "1"

    asyncio.run(b.handle_event(_message("!hablarchat", "owner")))

    assert b.shared_state.values["chat_silence"] == "0"
    assert len(b.platform.sent) == 1


def test_owner_can_toggle_silence_with_commands():
    b=bot()

    asyncio.run(b.handle_event(_message("!silenciarchat", "owner")))
    assert b.shared_state.values["chat_silence"] == "1"

    asyncio.run(b.handle_event(_message("!despertar", "owner")))
    assert b.shared_state.values["chat_silence"] == "0"


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


def test_unmentioned_followup_stays_silent_after_a_direct_stella_call():
    b = bot()
    calls = []

    async def capture_ai(*args):
        calls.append(args)

    b._ai_assist = capture_ai
    asyncio.run(b.handle_event({
        "type": "message",
        "threadId": "r",
        "message": {"content": "Stella", "author": {"uid": "u", "nickname": "Persona"}},
    }))
    b.platform.sent.clear()

    asyncio.run(b.handle_event({
        "type": "message",
        "threadId": "r",
        "message": {"content": "Me siento cansado hoy", "author": {"uid": "u", "nickname": "Persona"}},
    }))

    assert calls == []
    assert b.platform.sent == []


def test_structured_mention_allows_a_natural_response(monkeypatch):
    import stella.bot as bot_module

    b = bot()
    calls = []

    async def capture_ai(*args):
        calls.append(args)

    b._ai_assist = capture_ai
    monkeypatch.setattr(bot_module, "interpret_natural_request", lambda _value: None)

    asyncio.run(b.handle_event({
        "type": "message",
        "threadId": "r",
        "message": {
            "content": "¿Qué opinas de esto?",
            "mentions": [{"uid": "stella-account"}],
            "author": {"uid": "u", "nickname": "Persona"},
        },
    }))

    assert len(calls) == 1
    assert calls[0][3] == "¿Qué opinas de esto?"


def test_reply_to_stella_allows_a_natural_followup(monkeypatch):
    import stella.bot as bot_module

    b = bot()
    calls = []

    async def capture_ai(*args):
        calls.append(args)

    b._ai_assist = capture_ai
    monkeypatch.setattr(bot_module, "interpret_natural_request", lambda _value: None)

    asyncio.run(b.handle_event({
        "type": "message",
        "threadId": "r",
        "message": {
            "content": "¿Y qué piensas?",
            "replyMessageId": "stella-msg",
            "author": {"uid": "u", "nickname": "Persona"},
        },
    }))

    assert len(calls) == 1
    assert calls[0][3] == "¿Y qué piensas?"


def test_unaddressed_messages_are_not_read_aloud_even_when_voice_reading_is_enabled():
    b = bot()
    b.voice.enabled = True

    asyncio.run(b.handle_event({
        "type": "message",
        "threadId": "r",
        "message": {"content": "Hola a todos", "author": {"uid": "u", "nickname": "Persona"}},
    }))

    assert b.platform.sent == []
    assert b.voice.speech == []


@pytest.mark.parametrize("content", [
    "Stella, eres una idiota",
    "Stella, qué tonta eres jajaja",
    "Stella, cállate payasa",
    "Stella, no sirves para nada XD",
])
def test_playful_insults_are_routed_to_ai_for_a_comeback(content):
    b = bot()
    calls = []

    async def capture_ai(*args):
        calls.append(args)

    b._ai_assist = capture_ai
    asyncio.run(b.handle_event({
        "type": "message",
        "threadId": "r",
        "message": {"content": content, "author": {"uid": "u", "nickname": "Persona"}},
    }))

    assert len(calls) == 1
    expected_message = content.removeprefix("Stella, ").strip()
    assert "CONTEXTO DE CONVERSACIÓN INFORMAL" in calls[0][3]
    assert calls[0][3].endswith("Mensaje recibido: " + expected_message)
    assert b.platform.sent == []


@pytest.mark.parametrize("content", [
    "Stella, era broma jajaja",
    "Stella, te gané XD",
    "Stella, no te piques, te estoy molestando",
])
def test_teasing_is_routed_to_ai_for_playful_banter(content):
    b = bot()
    calls = []

    async def capture_ai(*args):
        calls.append(args)

    b._ai_assist = capture_ai
    asyncio.run(b.handle_event({
        "type": "message",
        "threadId": "r",
        "message": {"content": content, "author": {"uid": "u", "nickname": "Persona"}},
    }))

    assert len(calls) == 1
    expected_message = content.removeprefix("Stella, ").strip()
    assert "CONTEXTO DE CONVERSACIÓN INFORMAL" in calls[0][3]
    assert calls[0][3].endswith("Mensaje recibido: " + expected_message)


@pytest.mark.parametrize("content", [
    "Stella, no es gracioso, me hiciste sentir mal",
    "Stella, estoy llorando y necesito ayuda",
    "Stella, para de bromear, eso me dolió",
])
def test_distress_or_real_complaints_do_not_trigger_playful_comebacks(content):
    b = bot()
    calls = []

    async def capture_ai(*args):
        calls.append(args)

    b._ai_assist = capture_ai
    asyncio.run(b.handle_event({
        "type": "message",
        "threadId": "r",
        "message": {"content": content, "author": {"uid": "u", "nickname": "Persona"}},
    }))

    assert len(calls) == 1
    assert "contraataque ingenioso" not in calls[0][3]


def test_reply_formatter_converts_visible_escaped_newlines_to_real_lines():
    from stella.text import format_stella_text

    sample = r"En la penumbra del día, la noche se abre al sol,\nun susurro de estrellas en la bruma del farol."
    formatted = format_stella_text(sample)

    assert r"\n" not in formatted
    assert len(formatted.splitlines()) == 2



def test_reply_to_another_member_does_not_block_a_command():
    b = bot()
    calls = []

    async def capture(*args):
        calls.append(args)

    b._command = capture
    asyncio.run(b.handle_event({
        "type": "message",
        "threadId": "r",
        "message": {
            "content": "!ping",
            "replyMessageId": "other-user-message",
            "replyMessage": {
                "messageId": "other-user-message",
                "author": {"uid": "other-user", "nickname": "Otra persona"},
                "content": "mensaje anterior",
            },
            "author": {"uid": "u", "nickname": "Persona"},
        },
    }))
    assert len(calls) == 1
    assert calls[0][3] == "ping"


def test_reply_command_after_leading_user_mention_is_executed():
    b = bot()
    calls = []

    async def capture(*args):
        calls.append(args)

    b._command = capture
    asyncio.run(b.handle_event({
        "type": "message",
        "threadId": "r",
        "message": {
            "content": "@OtraPersona !ping",
            "replyMessage": {
                "messageId": "other-user-message",
                "author": {"uid": "other-user", "nickname": "OtraPersona"},
                "content": "mensaje anterior",
            },
            "author": {"uid": "u", "nickname": "Persona"},
        },
    }))
    assert len(calls) == 1
    assert calls[0][3] == "ping"


def test_mention_command_targets_replied_to_author():
    b = bot()
    asyncio.run(b.handle_event({
        "type": "message",
        "threadId": "r",
        "message": {
            "content": "!mencionar ven a ver esto",
            "replyMessage": {
                "messageId": "target-message",
                "author": {"uid": "target-uid", "nickname": "OtraPersona"},
                "content": "¿alguien vio esto?",
            },
            "author": {"uid": "sender-uid", "nickname": "Persona"},
        },
    }))
    assert len(b.platform.sent) == 1
    sent = b.platform.sent[0]
    assert sent[1].endswith("@OtraPersona ven a ver esto")


def test_mention_command_uses_structured_mentions_and_deduplicates_targets():
    b = bot()
    asyncio.run(b.handle_event({
        "type": "message",
        "threadId": "r",
        "message": {
            "content": "!mention",
            "mentions": [
                {"uid": "target-uid", "nickname": "Target"},
                {"uid": "target-uid", "nickname": "Target"},
                {"uid": "sender-uid", "nickname": "Sender"},
            ],
            "author": {"uid": "sender-uid", "nickname": "Sender"},
        },
    }))
    assert len(b.platform.sent) == 1
    assert b.platform.sent[0][1].endswith("@Target")



def test_social_command_reply_automatically_targets_quoted_author():
    b = bot()
    b.platform.read_messages["90210"] = {
        "messageId": "90210",
        "content": "hola",
        "author": {"uid": "target-uid", "nickname": "Persona X"},
    }

    asyncio.run(b.handle_event({
        "type": "message",
        "threadId": "r",
        "trigger": None,
        "message": {
            "content": "!besar",
            "replyMessageId": "90210",
            "author": {"uid": "sender-uid", "nickname": "Persona"},
        },
    }))

    assert b.platform.read_requests == [("r", "90210")]
    assert len(b.platform.sent) == 1
    response = b.platform.sent[0][1]
    assert "le da un beso a @Persona X" in response
    assert "Uso:" not in response


def test_social_command_reply_uses_embedded_quoted_author_without_extra_api_call():
    b = bot()
    asyncio.run(b.handle_event({
        "type": "message",
        "threadId": "r",
        "trigger": None,
        "message": {
            "content": "!besar",
            "replyMessage": {
                "messageId": "90211",
                "content": "hola",
                "author": {"uid": "target-uid", "nickname": "Persona Y"},
            },
            "author": {"uid": "sender-uid", "nickname": "Persona"},
        },
    }))

    assert b.platform.read_requests == []
    assert len(b.platform.sent) == 1
    assert "le da un beso a @Persona Y" in b.platform.sent[0][1]
    assert "Uso:" not in b.platform.sent[0][1]


def test_explicit_social_command_target_overrides_replied_author():
    b = bot()
    b.platform.read_messages["90212"] = {
        "messageId": "90212",
        "author": {"uid": "reply-uid", "nickname": "Persona del reply"},
    }
    asyncio.run(b.handle_event({
        "type": "message",
        "threadId": "r",
        "message": {
            "content": "!besar @OtraPersona",
            "replyMessageId": "90212",
            "author": {"uid": "sender-uid", "nickname": "Persona"},
        },
    }))
    assert len(b.platform.sent) == 1
    assert "le da un beso a @OtraPersona" in b.platform.sent[0][1]


def test_affinity_and_duel_can_use_the_replied_to_author():
    b = bot()
    b.platform.read_messages["90213"] = {
        "messageId": "90213",
        "author": {"uid": "target-uid", "nickname": "Persona X"},
    }
    b.store.get_affinity = lambda *args: 7
    asyncio.run(b.handle_event({
        "type": "message",
        "threadId": "r",
        "message": {
            "content": "!afinidad",
            "replyMessageId": "90213",
            "author": {"uid": "sender-uid", "nickname": "Persona"},
        },
    }))
    assert "Afinidad con Persona X" in b.platform.sent[-1][1]
    assert b.platform.read_requests == [("r", "90213")]



def test_prefixed_command_executes_when_replying_to_another_member():
    b = bot()
    calls = []

    async def capture(*args):
        calls.append(args)

    b._command = capture
    asyncio.run(b.handle_event({
        "type": "message",
        "threadId": "r",
        "trigger": None,
        "message": {
            "content": "!ping",
            "replyMessageId": "90220",
            "author": {"uid": "sender-uid", "nickname": "Persona"},
        },
    }))
    assert len(calls) == 1
    assert calls[0][3] == "ping"
    assert b.platform.sent == []


def test_owner_can_activate_and_resume_global_silence_with_natural_language():
    b = bot()

    asyncio.run(
        b.handle_event(
            _message("Stella, activa el silencio global.", "owner")
        )
    )
    assert b.shared_state.values["chat_silence"] == "1"

    asyncio.run(
        b.handle_event(
            _message("Stella, desactiva el silencio global.", "owner")
        )
    )
    assert b.shared_state.values["chat_silence"] == "0"



def test_global_silence_blocks_voice_button_interactions():
    b = bot()
    b.shared_state.values["chat_silence"] = "1"

    asyncio.run(
        b.handle_event({
            "type": "interaction",
            "threadId": "room-a",
            "user": {"uid": "member"},
            "buttonId": "voice-action",
        })
    )

    assert b.voice.interactions == []
    assert b.voice.speech_stopped is True



def test_sleep_social_action_does_not_assume_the_sender_gender():
    b = bot()

    asyncio.run(
        b._social_action(
            "room-a",
            "owner",
            "Alex",
            "@Persona",
            {"_stella_social_action": "dormir"},
        )
    )

    assert b.platform.sent
    assert "Alex se queda junto a y acompaña a @Persona." in b.platform.sent[0][1]
    assert "dormido" not in b.platform.sent[0][1]
    assert "dormida" not in b.platform.sent[0][1]


def test_proactive_participation_is_off_by_default():
    b = bot()
    b._proactive_cooldown = {}
    calls = []

    async def fake_ask(prompt, *, system=None):
        calls.append(prompt)
        return "Yo empezaría comparando las necesidades del proyecto."

    b.ai = SimpleNamespace(ask=fake_ask)

    asyncio.run(b._maybe_proactive_reply(
        "room-a",
        "member",
        "Alex",
        "¿Alguien sabe qué base de datos conviene para un proyecto pequeño?",
        {"messageId": "msg-1"},
    ))

    assert calls == []
    assert b.platform.sent == []


def test_proactive_participation_only_answers_clear_room_invitations_and_respects_cooldown():
    b = bot()
    b._proactive_cooldown = {}
    b.shared_state.values["stella_proactive:room-a"] = "1"
    calls = []

    async def fake_ask(prompt, *, system=None):
        calls.append((prompt, system))
        return "Yo empezaría comparando las necesidades, el volumen de datos y el presupuesto."

    b.ai = SimpleNamespace(ask=fake_ask)
    invitation = "¿Alguien sabe qué base de datos conviene para un proyecto pequeño?"
    message = {"messageId": "msg-1"}

    asyncio.run(b._maybe_proactive_reply("room-a", "member", "Alex", invitation, message))
    asyncio.run(b._maybe_proactive_reply("room-a", "member-2", "Sam", invitation, {"messageId": "msg-2"}))

    assert len(calls) == 1
    assert "no ejecutes" in calls[0][1].casefold()
    assert len(b.platform.sent) == 1
    assert "presupuesto" in b.platform.sent[0][1].casefold()


def test_proactive_participation_ignores_ordinary_chat_without_an_invitation():
    b = bot()
    b._proactive_cooldown = {}
    b.shared_state.values["stella_proactive:room-a"] = "1"
    calls = []

    async def fake_ask(prompt, *, system=None):
        calls.append(prompt)
        return "No debería responder."

    b.ai = SimpleNamespace(ask=fake_ask)

    asyncio.run(b._maybe_proactive_reply(
        "room-a", "member", "Alex", "Hoy estoy jugando Minecraft con mis amigos.", {}
    ))

    assert calls == []
    assert b.platform.sent == []



def test_proactive_presence_setting_is_admin_only_and_persisted():
    b = bot()
    b.settings.additional_admin_uids = ""
    b.settings.admin_uids = "owner"
    b._proactive_enabled_cache = {}
    b._proactive_cooldown = {}

    asyncio.run(b._stella_presence("room-a", "member", "Persona", "activar", {}))
    assert b.shared_state.values.get("stella_proactive:room-a") is None

    asyncio.run(b._stella_presence("room-a", "owner", "Owner", "activar", {}))
    assert b.shared_state.values["stella_proactive:room-a"] == "1"
    assert asyncio.run(b._proactive_enabled("room-a")) is True

    asyncio.run(b._stella_presence("room-a", "owner", "Owner", "desactivar", {}))
    assert b.shared_state.values["stella_proactive:room-a"] == "0"
    assert asyncio.run(b._proactive_enabled("room-a")) is False
