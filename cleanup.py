"""Watch-based media cleanup tracking.

Tracks every season/movie added via Media Bot so a periodic cleanup job can
later check Tautulli watch history and delete content that has gone unwatched
beyond the configured window.

Two tables are maintained in cleanup.db:

``cleanup_tracking``
    One row per tracked item (a movie or a specific TV season).  The
    ``status`` column can be:

    - ``pending``   — actively monitoring; cleanup job will re-check.
    - ``deleted``   — files and arr record have been removed.
    - ``protected`` — manually excluded from cleanup.

``deletion_notifications``
    Short messages queued for delivery to a user the next time they chat.
    Cleared when ``mark_notifications_delivered`` is called.

Usage::

    from cleanup import CleanupDB

    db = CleanupDB()
    db.record_addition(
        media_type="movie",
        arr_id=42,
        title="Inception",
        requester_username="alice",
        requester_plex_id="123",
    )
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import config

log = logging.getLogger(__name__)

_DB_PATH = Path(config.DATA_DIR) / "cleanup.db"


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), timeout=20.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cleanup_tracking (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            media_type            TEXT    NOT NULL,
            arr_id                INTEGER NOT NULL,
            title                 TEXT    NOT NULL,
            requester_username    TEXT    NOT NULL,
            requester_plex_id     TEXT    NOT NULL,
            season_number         INTEGER,
            added_date            TEXT    NOT NULL,
            last_check_date       TEXT,
            check_count           INTEGER NOT NULL DEFAULT 0,
            status                TEXT    NOT NULL DEFAULT 'pending',
            notified              INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_ct_status
            ON cleanup_tracking (status, added_date);

        CREATE INDEX IF NOT EXISTS idx_ct_requester
            ON cleanup_tracking (requester_plex_id, status);

        CREATE TABLE IF NOT EXISTS deletion_notifications (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            plex_user_id    TEXT    NOT NULL,
            message         TEXT    NOT NULL,
            created_at      TEXT    NOT NULL,
            delivered       INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_dn_user_delivered
            ON deletion_notifications (plex_user_id, delivered);
    """)
    conn.commit()


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class CleanupDB:
    """Thin wrapper around the cleanup SQLite database."""

    def __init__(self) -> None:
        conn = _get_connection()
        try:
            _init_db(conn)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    def record_addition(
        self,
        *,
        media_type: str,
        arr_id: int,
        title: str,
        requester_username: str,
        requester_plex_id: str,
        season_number: int | None = None,
    ) -> None:
        """Record a newly added movie or TV season for future cleanup checks."""
        if not config.CLEANUP_ENABLED:
            return
        if media_type not in ("movie", "series_season"):
            log.warning("cleanup.record_addition: unknown media_type %r", media_type)
            return

        conn = _get_connection()
        try:
            if media_type == "movie":
                exists = conn.execute(
                    "SELECT id FROM cleanup_tracking WHERE arr_id=? AND media_type='movie' AND status='pending'",
                    (arr_id,),
                ).fetchone()
            else:
                exists = conn.execute(
                    "SELECT id FROM cleanup_tracking WHERE arr_id=? AND season_number=? AND media_type='series_season' AND status='pending'",
                    (arr_id, season_number),
                ).fetchone()

            if exists:
                return

            conn.execute(
                """
                INSERT INTO cleanup_tracking
                (media_type, arr_id, title, requester_username, requester_plex_id, season_number, added_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    media_type,
                    arr_id,
                    title,
                    requester_username,
                    requester_plex_id,
                    season_number,
                    _today_utc(),
                ),
            )
            conn.commit()
            log.info("cleanup.tracked_addition type=%s title=%r requester=%s", media_type, title, requester_username)
        finally:
            conn.close()

    def mark_checked(self, row_id: int) -> None:
        """Update last_check_date and increment check_count for a row."""
        conn = _get_connection()
        try:
            conn.execute(
                "UPDATE cleanup_tracking SET last_check_date=?, check_count=check_count+1 WHERE id=?",
                (_today_utc(), row_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_deleted(self, row_id: int) -> None:
        """Mark a tracked item as deleted."""
        conn = _get_connection()
        try:
            conn.execute(
                "UPDATE cleanup_tracking SET status='deleted', last_check_date=? WHERE id=?",
                (_today_utc(), row_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_protected(self, row_id: int) -> None:
        """Exclude a tracked item from future cleanup checks."""
        conn = _get_connection()
        try:
            conn.execute(
                "UPDATE cleanup_tracking SET status='protected' WHERE id=?",
                (row_id,),
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def get_pending_checks(self) -> list[dict]:
        """Return all items that are due for a cleanup pass.
        
        Only returns items where it has been at least 1 day since the
        previous check or the last check was at least
        ``CLEANUP_CHECK_INTERVAL_DAYS`` ago.
        """
        interval = max(1, config.CLEANUP_CHECK_INTERVAL_DAYS)
        conn = _get_connection()
        try:
            rows = conn.execute(
                """
                SELECT * FROM cleanup_tracking
                WHERE status = 'pending'
                  AND (
                      last_check_date IS NULL
                      OR cast(julianday(?) - julianday(last_check_date) as integer) >= ?
                  )
                ORDER BY added_date ASC
                """,
                (_today_utc(), interval),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_all_pending_for_user(self, plex_user_id: str) -> list[dict]:
        """Return all pending rows for a given Plex user ID."""
        conn = _get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM cleanup_tracking WHERE requester_plex_id=? AND status='pending' ORDER BY added_date ASC",
                (plex_user_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_pending_series_seasons(self, arr_id: int) -> list[dict]:
        """Return all pending season rows for a series arr_id."""
        conn = _get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM cleanup_tracking WHERE arr_id=? AND media_type='series_season' AND status='pending'",
                (arr_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Deletion notifications for the user
    # ------------------------------------------------------------------

    def queue_deletion_notification(self, plex_user_id: str, message: str) -> None:
        """Store a deletion notice for delivery on the user's next chat."""
        conn = _get_connection()
        try:
            conn.execute(
                "INSERT INTO deletion_notifications (plex_user_id, message, created_at) VALUES (?, ?, ?)",
                (plex_user_id, message, _now_utc()),
            )
            conn.commit()
        finally:
            conn.close()

    def get_undelivered_notifications(self, plex_user_id: str) -> list[dict]:
        """Return undelivered deletion notices for a user."""
        conn = _get_connection()
        try:
            rows = conn.execute(
                "SELECT id, message FROM deletion_notifications WHERE plex_user_id=? AND delivered=0 ORDER BY created_at ASC",
                (plex_user_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def mark_notifications_delivered(self, plex_user_id: str) -> None:
        """Mark all pending notices for a user as delivered."""
        conn = _get_connection()
        try:
            conn.execute(
                "UPDATE deletion_notifications SET delivered=1 WHERE plex_user_id=? AND delivered=0",
                (plex_user_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def get_unwatched_backlog_titles(self, plex_user_id: str) -> list[str]:
        """Return titles of completely unwatched content older than warn_days.
        
        This is used to quickly inject a warning into the agent response
        at download time.  Does NOT call Tautulli — that check happens in the
        cleanup service; this is a fast DB-only look-up.
        """
        warn_days = max(1, config.CLEANUP_BACKLOG_WARN_DAYS)
        conn = _get_connection()
        try:
            rows = conn.execute(
                """
                SELECT DISTINCT title FROM cleanup_tracking
                WHERE requester_plex_id = ?
                  AND status = 'pending'
                  AND cast(julianday(?) - julianday(added_date) as integer) >= ?
                ORDER BY added_date ASC
                LIMIT 5
                """,
                (plex_user_id, _today_utc(), warn_days),
            ).fetchall()
            return [r['title'] for r in rows]
        finally:
            conn.close()
