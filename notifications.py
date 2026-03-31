"""Download notification tracking.

Stores pending download requests and completed download notifications per user.
Notifications are delivered on the user's next chat interaction or via /notifications polling.

Usage example::

    import notifications

    # After successfully requesting a download
    notifications.record_pending_download(user_id="123", username="alice", title="Inception", media_type="movie")

    # When a Radarr/Sonarr webhook fires (download complete)
    result = notifications.find_requesting_user("Inception", "movie")
    if result:
        user_id, username = result
    else:
        user_id = "__owner__"
    notifications.store_notification(user_id, "Inception", "movie", "downloaded",
                                     "✅ Inception is now available in Plex!")

    # When delivering notifications to a user
    pending = notifications.get_pending_notifications("123")
    notifications.mark_delivered([n["id"] for n in pending])
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path

import config

log = logging.getLogger(__name__)

_DB_PATH = Path(config.DATA_DIR) / "notifications.db"
_lock = threading.Lock()


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pending_downloads (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      TEXT    NOT NULL,
            username     TEXT    NOT NULL,
            title        TEXT    NOT NULL,
            title_lower  TEXT    NOT NULL,
            media_type   TEXT    NOT NULL,
            requested_at INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_pd_title ON pending_downloads(title_lower);

        CREATE TABLE IF NOT EXISTS download_notifications (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      TEXT    NOT NULL,
            title        TEXT    NOT NULL,
            media_type   TEXT    NOT NULL,
            event_type   TEXT    NOT NULL,
            message      TEXT    NOT NULL,
            timestamp    INTEGER NOT NULL,
            delivered    INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_dn_user ON download_notifications(user_id, delivered);
    """)
    conn.commit()


def _ensure_db() -> None:
    with _lock:
        conn = _get_connection()
        try:
            _init_db(conn)
        finally:
            conn.close()


# Initialise on import so tables exist before any call is made.
_ensure_db()


def record_pending_download(user_id: str, username: str, title: str, media_type: str) -> None:
    """Record that a user has requested a download, to route completion notifications back to them.

    Args:
        user_id:    Unique identifier for the user (Plex user ID or ``"api_key"``).
        username:   Display name used for logging.
        title:      Human-readable media title (movie title or series title).
        media_type: ``"movie"`` or ``"tv_season"``.
    """
    now = int(time.time())
    with _lock:
        conn = _get_connection()
        try:
            conn.execute(
                "INSERT INTO pending_downloads (user_id, username, title, title_lower, media_type, requested_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, username, title, title.lower().strip(), media_type, now),
            )
            conn.commit()
        finally:
            conn.close()
    log.info(
        "notifications.pending_recorded",
        extra={"user_id": user_id, "title": title, "media_type": media_type},
    )


def find_requesting_user(title: str, media_type: str) -> tuple[str, str] | None:
    """Find the user who most recently requested a download of a given title.

    Removes the matched record so it cannot be matched again.

    Args:
        title:      Media title to look up (case-insensitive).
        media_type: ``"movie"`` or ``"tv_season"``.

    Returns:
        ``(user_id, username)`` tuple, or ``None`` if no pending request found.
    """
    title_lower = title.lower().strip()
    with _lock:
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT id, user_id, username FROM pending_downloads "
                "WHERE title_lower = ? AND media_type = ? "
                "ORDER BY requested_at DESC LIMIT 1",
                (title_lower, media_type),
            ).fetchone()
            if row:
                conn.execute("DELETE FROM pending_downloads WHERE id = ?", (row["id"],))
                conn.commit()
                return row["user_id"], row["username"]
        finally:
            conn.close()
    return None


def store_notification(user_id: str, title: str, media_type: str, event_type: str, message: str) -> None:
    """Store a notification for delivery to a user on their next interaction.

    Duplicate notifications (same user, title, and event_type within the last hour)
    are silently discarded to prevent spamming when a series download triggers
    multiple webhook calls.

    Args:
        user_id:    Target user ID, or ``"__owner__"`` for owner-only alerts.
        title:      Media title associated with the notification.
        media_type: ``"movie"``, ``"tv_season"``, or ``"system"``.
        event_type: ``"downloaded"``, ``"download_failed"``, or ``"health"``.
        message:    Human-readable notification text.
    """
    now = int(time.time())
    one_hour_ago = now - 3600
    with _lock:
        conn = _get_connection()
        try:
            # Deduplicate: skip if an identical undelivered notification was already stored in the last hour
            existing = conn.execute(
                "SELECT id FROM download_notifications "
                "WHERE user_id = ? AND title = ? AND event_type = ? AND delivered = 0 AND timestamp > ?",
                (user_id, title, event_type, one_hour_ago),
            ).fetchone()
            if existing:
                return
            conn.execute(
                "INSERT INTO download_notifications (user_id, title, media_type, event_type, message, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, title, media_type, event_type, message, now),
            )
            conn.commit()
        finally:
            conn.close()
    log.info(
        "notifications.stored",
        extra={"user_id": user_id, "title": title, "event_type": event_type},
    )


def get_pending_notifications(user_id: str) -> list[dict]:
    """Return all undelivered notifications for a specific user.

    Does *not* mark them as delivered — call :func:`mark_delivered` after
    successfully sending the notifications to the user.

    Args:
        user_id: Target user ID (Plex ID string).

    Returns:
        List of notification dicts with keys: ``id``, ``title``, ``media_type``,
        ``event_type``, ``message``, ``timestamp``.
    """
    with _lock:
        conn = _get_connection()
        try:
            rows = conn.execute(
                "SELECT id, title, media_type, event_type, message, timestamp "
                "FROM download_notifications "
                "WHERE user_id = ? AND delivered = 0 "
                "ORDER BY timestamp ASC",
                (user_id,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()


def get_owner_pending_notifications() -> list[dict]:
    """Return undelivered owner-targeted notifications (health alerts, unmatched events).

    These are stored with ``user_id='__owner__'`` and should be shown to any
    authenticated user with ``is_owner=True``.

    Returns:
        List of notification dicts.
    """
    with _lock:
        conn = _get_connection()
        try:
            rows = conn.execute(
                "SELECT id, title, media_type, event_type, message, timestamp "
                "FROM download_notifications "
                "WHERE user_id = '__owner__' AND delivered = 0 "
                "ORDER BY timestamp ASC",
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()


def mark_delivered(notification_ids: list[int]) -> None:
    """Mark the given notifications as delivered so they are not shown again.

    Args:
        notification_ids: List of ``id`` values from notification dicts.
    """
    if not notification_ids:
        return
    # Validate all IDs are integers to prevent any injection risk
    validated_ids = [int(nid) for nid in notification_ids]
    with _lock:
        conn = _get_connection()
        try:
            conn.execute(
                f"UPDATE download_notifications SET delivered = 1 "
                f"WHERE id IN ({','.join('?' * len(validated_ids))})",
                validated_ids,
            )
            conn.commit()
        finally:
            conn.close()
