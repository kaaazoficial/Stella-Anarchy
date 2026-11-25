"""Bounded retention for Stella's operational community features.

No message bodies, conversation history or permanent AI memory are stored.
"""
from __future__ import annotations

from datetime import date, timedelta

USER_INACTIVITY_DAYS = 90
PROFILE_TEXT_DAYS = 30
ACTIVITY_RETENTION_DAYS = 7
GIFT_RETENTION_DAYS = 30
GIFT_MAX_PER_RECIPIENT = 5
MODERATION_RETENTION_DAYS = 90
POLL_RETENTION_DAYS = 30
EVENT_RETENTION_DAYS = 30


def cleanup_database_retention(db, *, now: float, today: date) -> dict[str, int]:
    user_cutoff = float(now) - USER_INACTIVITY_DAYS * 86400
    profile_cutoff = float(now) - PROFILE_TEXT_DAYS * 86400
    gift_cutoff = float(now) - GIFT_RETENTION_DAYS * 86400
    moderation_cutoff = float(now) - MODERATION_RETENTION_DAYS * 86400
    poll_cutoff = float(now) - POLL_RETENTION_DAYS * 86400
    event_cutoff = float(now) - EVENT_RETENTION_DAYS * 86400
    activity_cutoff = (today - timedelta(days=ACTIVITY_RETENTION_DAYS - 1)).isoformat()
    counts: dict[str, int] = {}

    cursor = db.execute(
        """UPDATE users SET bio='', status='', profile_updated_at=NULL
           WHERE profile_updated_at IS NOT NULL AND profile_updated_at < ?
             AND (bio <> '' OR status <> '')""",
        (profile_cutoff,),
    )
    counts["profile_texts_expired"] = max(0, cursor.rowcount)

    statements = (
        ("affinities_expired",
         "DELETE FROM affinities WHERE uid IN (SELECT uid FROM users WHERE last_seen < ?) OR target_uid IN (SELECT uid FROM users WHERE last_seen < ?)",
         (user_cutoff, user_cutoff)),
        ("gifts_expired",
         "DELETE FROM gifts WHERE sender_uid IN (SELECT uid FROM users WHERE last_seen < ?) OR target_uid IN (SELECT uid FROM users WHERE last_seen < ?) OR created_at < ?",
         (user_cutoff, user_cutoff, gift_cutoff)),
        ("reward_claims_expired",
         "DELETE FROM reward_claims WHERE uid IN (SELECT uid FROM users WHERE last_seen < ?)",
         (user_cutoff,)),
        ("moderation_warnings_expired",
         "DELETE FROM moderation_warnings WHERE target_uid IN (SELECT uid FROM users WHERE last_seen < ?) OR moderator_uid IN (SELECT uid FROM users WHERE last_seen < ?) OR created_at < ?",
         (user_cutoff, user_cutoff, moderation_cutoff)),
        ("activity_expired",
         "DELETE FROM activity_daily WHERE day_key < ?",
         (activity_cutoff,)),
        ("old_polls_expired",
         "DELETE FROM polls WHERE created_at < ?",
         (poll_cutoff,)),
        ("old_events_expired",
         "DELETE FROM community_events WHERE starts_at < ?",
         (event_cutoff,)),
    )
    for key, sql, params in statements:
        cursor = db.execute(sql, params)
        counts[key] = max(0, cursor.rowcount)

    cursor = db.execute("DELETE FROM users WHERE last_seen < ?", (user_cutoff,))
    counts["inactive_users_deleted"] = max(0, cursor.rowcount)

    cursor = db.execute(
        """DELETE FROM gifts WHERE id NOT IN (
             SELECT id FROM gifts AS recent
             WHERE recent.target_uid = gifts.target_uid
             ORDER BY recent.id DESC LIMIT ?
           )""",
        (GIFT_MAX_PER_RECIPIENT,),
    )
    counts["excess_gifts_deleted"] = max(0, cursor.rowcount)
    return counts
