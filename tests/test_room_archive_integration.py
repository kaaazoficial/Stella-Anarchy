from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stella.bot import StellaBot


class ArchiveRecorder:
    def __init__(self):
        self.events = []

    async def record_event(self, room_id, event_type, **fields):
        self.events.append((room_id, event_type, fields))
        return True


def test_every_authenticated_webhook_event_is_archived_before_event_routing():
    async def scenario():
        bot = object.__new__(StellaBot)
        archive = ArchiveRecorder()
        bot.room_archive = archive

        await bot.handle_event({
            "type": "future.room.feature",
            "threadId": "room-191",
            "eventId": "evt-future",
            "eventTime": "2026-11-26T18:00:00Z",
            "user": {"uid": "member-uid", "nickname": "Miembro"},
            "payload": {"description": "evento aún no reconocido por el bot"},
        })

        assert len(archive.events) == 1
        room_id, event_type, fields = archive.events[0]
        assert room_id == "room-191"
        assert event_type == "anarchy.future.room.feature"
        assert fields["source_event_id"] == "evt-future"
        assert fields["actor_uid"] == "member-uid"
        assert fields["actor_name"] == "Miembro"
        assert fields["metadata"]["webhook_event"]["payload"]["description"] == (
            "evento aún no reconocido por el bot"
        )

    asyncio.run(scenario())


def test_all_standard_message_lifecycle_events_are_mapped_for_archive():
    async def scenario():
        bot = object.__new__(StellaBot)
        archive = ArchiveRecorder()
        bot.room_archive = archive

        events = [
            ("message", "chat.message.in"),
            ("message.created", "chat.message.in"),
            ("message_create", "chat.message.in"),
            ("message.edited", "chat.message.edited"),
            ("message.deleted", "chat.message.deleted"),
            ("message_delete", "chat.message.deleted"),
            ("interaction", "chat.interaction"),
            ("room.join", "chat.room.join"),
            ("room.leave", "chat.room.leave"),
            ("room.speaker", "chat.room.speaker"),
        ]
        for index, (kind, _) in enumerate(events):
            await bot.handle_event({
                "type": kind,
                "threadId": "room-191",
                "eventId": f"evt-{index}",
                "eventTime": "2026-11-26T18:00:00Z",
                "message": {
                    "messageId": str(index + 1),
                    "content": f"contenido-{kind}",
                    "author": {"uid": "member-uid", "nickname": "Miembro"},
                },
            })

        assert [event[1] for event in archive.events] == [expected for _, expected in events]
        for room_id, _, fields in archive.events:
            assert room_id == "room-191"
            assert fields["metadata"]["webhook_event"]["type"]

    asyncio.run(scenario())
