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
    def touch_user(self,*a): pass
    def record_activity(self,*a): pass
    def add_warning(self,*a): return 1
    def get_setting(self,*a): return "1"
    def auto_reply_rules(self,*a): return []

class Voice:
    def __init__(self): self.speech=[]; self.enabled=False
    async def enqueue_speech(self,*a):
        if self.enabled: self.speech.append(a)
    async def leave_room(self,*a,**k): pass

def bot():
    value=object.__new__(StellaBot)
    value.settings=SimpleNamespace(command_prefix="!")
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

def test_bare_name_is_a_direct_call():
    b=bot()
    asyncio.run(b.handle_event({"type":"message","threadId":"r","message":{"content":"Stella","author":{"uid":"u","nickname":"Persona"}}}))
    assert len(b.platform.sent)==1 and "Aquí estoy" in b.platform.sent[0][1]

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

