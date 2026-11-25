"""Privacy-first retention for Stella's operational community data."""
from __future__ import annotations

import time
from datetime import date

USER_INACTIVITY_DAYS = 90
PROFILE_TEXT_DAYS = 30
ACTIVITY_RETENTION_DAYS = 7
GIFT_RETENTION_DAYS = 30
GIFT_MAX_PER_RECIPIENT = 5
MODERATION_RETENTION_DAYS = 90
EVENT_RETENTION_DAYS = 30
POLL_RETENTION_DAYS = 30


def cleanup_database_retention(db, *, now: float | None = None, today: date | None = None) -> dict[str, int]:
    """Expire operational records; never stores or processes conversation transcripts."""
    stamp = time.time() if now is None else float(now)
    today = today or date.today()
    user_cutoff = stamp - USER_INACTIVITY_DAYS * 86400
    profile_cutoff = stamp - PROFILE_TEXT_DAYS * 86400
    gift_cutoff = stamp - GIFT_RETENTION_DAYS * 86400
    moderation_cutoff = stamp - MODERATION_RETENTION_DAYS * 86400
    event_cutoff = stamp - EVENT_RETENTION_DAYS * 86400
    poll_cutoff = stamp - POLL_RETENTION_DAYS * 86400
    activity_cutoff = (today.toordinal() - ACTIVITY_RETENTION_DAYS + 1)
    counts: dict[str, int] = {}

    def run(label: str, sql: str, parameters: tuple = ()) -> None:
        cursor = db.execute(sql, parameters)
        counts[label] = max(0, int(cursor.rowcount))

    run("profile_texts_expired",
        "UPDATE users SET bio='',status='',profile_updated_at=NULL WHERE profile_updated_at IS NOT NULL AND profile_updated_at < ? AND (bio<>'' OR status<>'')",
        (profile_cutoff,))
    run("gifts_expired", "DELETE FROM gifts WHERE created_at < ?", (gift_cutoff,))
    run("affinities_expired",
        "DELETE FROM affinities WHERE uid IN (SELECT uid FROM users WHERE last_seen < ?) OR target_uid IN (SELECT uid FROM users WHERE last_seen < ?)",
        (user_cutoff, user_cutoff))
    run("reward_claims_expired",
        "DELETE FROM reward_claims WHERE uid IN (SELECT uid FROM users WHERE last_seen < ?)",
        (user_cutoff,))
    run("playlist_tracks_expired",
        "DELETE FROM dj_playlist_tracks WHERE playlist_id IN (SELECT id FROM dj_playlists WHERE owner_uid IN (SELECT uid FROM users WHERE last_seen < ?))",
        (user_cutoff,))
    run("playlists_expired",
        "DELETE FROM dj_playlists WHERE owner_uid IN (SELECT uid FROM users WHERE last_seen < ?)",
        (user_cutoff,))
    run("warnings_expired", "DELETE FROM moderation_warnings WHERE created_at < ?", (moderation_cutoff,))
    run("activity_expired", "DELETE FROM activity_daily WHERE day_key < date(?, 'unixepoch')", (stamp - (ACTIVITY_RETENTION_DAYS - 1) * 86400,))
    run("event_rsvps_expired",
        "DELETE FROM event_rsvps WHERE event_id IN (SELECT id FROM community_events WHERE starts_at < ?)",
        (event_cutoff,))
    run("events_expired", "DELETE FROM community_events WHERE starts_at < ?", (event_cutoff,))
    run("poll_votes_expired",
        "DELETE FROM poll_votes WHERE poll_id IN (SELECT id FROM polls WHERE created_at < ?)",
        (poll_cutoff,))
    run("polls_expired", "DELETE FROM polls WHERE created_at < ?", (poll_cutoff,))
    run("inactive_users_deleted", "DELETE FROM users WHERE last_seen < ?", (user_cutoff,))
    run("excess_gifts_deleted",
        """DELETE FROM gifts WHERE id NOT IN (
             SELECT id FROM gifts AS recent
             WHERE recent.target_uid=gifts.target_uid
             ORDER BY recent.id DESC LIMIT ?
           )""",
        (GIFT_MAX_PER_RECIPIENT,))
    return counts
