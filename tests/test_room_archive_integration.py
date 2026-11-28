from __future__ import annotations

import asyncio

from stella.bot import StellaBot, archive_event_type


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


def test_standard_message_and_room_lifecycle_event_types_are_preserved():
    expected = {
        "message": "chat.message.in",
        "message.created": "chat.message.in",
        "message_create": "chat.message.in",
        "message.edited": "chat.message.edited",
        "message.deleted": "chat.message.deleted",
        "message_delete": "chat.message.deleted",
        "interaction": "chat.interaction",
        "room.ready": "chat.room.ready",
        "room.end": "chat.room.ended",
        "room.kicked": "chat.room.kicked",
        "thread.removed": "chat.room.removed",
        "room.join": "chat.room.join",
        "room.leave": "chat.room.leave",
        "room.speaker": "chat.room.speaker",
        "future.event": "anarchy.future.event",
    }

    for source, archived in expected.items():
        assert archive_event_type(source) == archived
