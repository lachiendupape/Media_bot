"""User download quota management.

Tracks per-user daily download counts (movies and TV seasons) using a SQLite
database. Quotas reset at UTC midnight. Per-user overrides can be stored in the
database; when no override exists the global defaults from ``config`` are used.

Usage example::

    from quota import check_quota, record_download

    # Before triggering a download
    allowed, message = check_quota(user_id="123", username="alice", media_type="movie")
    if not allowed:
        return message  # Quota exceeded — relay the message to the user

    # After a successful download
    record_download(user_id="123", username="alice", media_type="movie", title="Inception")
"""

import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import config

log = logging.getLogger(__name__)

_DB_PATH = Path(config.DATA_DIR) / "quotas.db"
_lock = threading.Lock()


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS download_events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       TEXT    NOT NULL,
            username      TEXT    NOT NULL,
            media_type    TEXT    NOT NULL,
            title         TEXT    NOT NULL,
            timestamp     INTEGER NOT NULL,
            date_utc      TEXT    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_de_user_date
            ON download_events (user_id, date_utc, media_type);

        CREATE TABLE IF NOT EXISTS user_quota_overrides (
            user_id               TEXT PRIMARY KEY,
            daily_movie_quota     INTEGER,
            daily_episode_quota   INTEGER
        );
    """)
    conn.commit()


def _ensure_db() -> None:
    """Initialise the database if it has not been set up yet."""
    with _lock:
        conn = _get_connection()
        try:
            _init_db(conn)
        finally:
            conn.close()


# Initialise on import so the tables exist before any call is made.
_ensure_db()


def _today_utc() -> str:
    """Return today's date in UTC as an ISO-8601 string (YYYY-MM-DD)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _normalize_media_type(media_type: str) -> str:
    """Normalize supported media types to internal counters."""
    if media_type == "movie":
        return "movie"
    if media_type in {"tv_series", "tv_season"}:
        return "tv_series"
    return media_type


def _get_limit(user_id: str, media_type: str) -> int:
    """Return the effective daily download limit for *user_id* and *media_type*.

    Checks for a per-user override first; falls back to the global default from
    ``config``.  A value of ``0`` means unlimited.
    """
    with _lock:
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT daily_movie_quota, daily_episode_quota FROM user_quota_overrides WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        finally:
            conn.close()

    normalized = _normalize_media_type(media_type)

    if row is not None:
        col = "daily_movie_quota" if normalized == "movie" else "daily_episode_quota"
        override = row[col]
        if override is not None:
            return int(override)

    if normalized == "movie":
        return config.DAILY_MOVIE_QUOTA
    return config.DAILY_TV_SERIES_QUOTA


def _count_today(user_id: str, media_type: str, date_utc: str) -> int:
    """Return how many downloads of *media_type* *user_id* has made today."""
    normalized = _normalize_media_type(media_type)

    with _lock:
        conn = _get_connection()
        try:
            if normalized == "tv_series":
                # Include legacy tv_season rows so old data still counts.
                row = conn.execute(
                    "SELECT COUNT(*) FROM download_events "
                    "WHERE user_id = ? AND media_type IN ('tv_series', 'tv_season') AND date_utc = ?",
                    (user_id, date_utc),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM download_events "
                    "WHERE user_id = ? AND media_type = ? AND date_utc = ?",
                    (user_id, normalized, date_utc),
                ).fetchone()
            return int(row[0])
        finally:
            conn.close()


def check_quota(user_id: str, username: str, media_type: str) -> tuple[bool, str]:
    """Check whether *user_id* is allowed to download another item of *media_type*.

    Args:
        user_id:    Unique identifier for the user (Plex user ID or ``"api_key:<hash>"``).
        username:   Display name used in the returned message.
        media_type: ``"movie"`` or ``"tv_series"``.

    Returns:
        ``(True, "")`` when the download is allowed.
        ``(False, <human-readable reason>)`` when the quota is exceeded.
    """
    if not config.QUOTA_ENABLED:
        return True, ""

    limit = _get_limit(user_id, media_type)
    if limit == 0:
        return True, ""

    today = _today_utc()
    used = _count_today(user_id, media_type, today)

    normalized = _normalize_media_type(media_type)

    if used >= limit:
        kind = "movie" if normalized == "movie" else "TV series"
        return (
            False,
            f"⚠️ You've reached your daily {kind} download quota ({used}/{limit}). "
            "Your quota resets at midnight UTC.",
        )

    return True, ""


def record_download(user_id: str, username: str, media_type: str, title: str) -> None:
    """Record a successful download event for *user_id*.

    Args:
        user_id:    Unique identifier for the user.
        username:   Display name (stored for auditing).
        media_type: ``"movie"`` or ``"tv_series"``.
        title:      Human-readable title of the downloaded media.
    """
    if not config.QUOTA_ENABLED:
        return

    normalized = _normalize_media_type(media_type)
    now = int(time.time())
    today = _today_utc()
    with _lock:
        conn = _get_connection()
        try:
            conn.execute(
                "INSERT INTO download_events (user_id, username, media_type, title, timestamp, date_utc) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, username, normalized, title, now, today),
            )
            conn.commit()
        finally:
            conn.close()


def get_user_usage(user_id: str) -> dict:
    """Return today's download counts for *user_id*.

    Returns a dict with keys ``movies``, ``tv_series``, ``movie_limit``,
    ``tv_series_limit``, and ``date_utc``.
    """
    today = _today_utc()
    movies = _count_today(user_id, "movie", today)
    tv_series = _count_today(user_id, "tv_series", today)
    return {
        "date_utc": today,
        "movies": movies,
        "movie_limit": _get_limit(user_id, "movie"),
        "tv_series": tv_series,
        "tv_series_limit": _get_limit(user_id, "tv_series"),
        # Legacy compatibility for existing callers.
        "tv_seasons": tv_series,
        "tv_season_limit": _get_limit(user_id, "tv_series"),
    }


def set_user_quota_override(user_id: str, daily_movie_quota: int | None, daily_episode_quota: int | None) -> None:
    """Set or update per-user quota overrides.

    Pass ``None`` to clear the override and fall back to the global default.
    Pass ``0`` to mark the quota as unlimited for that user.
    """
    with _lock:
        conn = _get_connection()
        try:
            conn.execute(
                """
                INSERT INTO user_quota_overrides (user_id, daily_movie_quota, daily_episode_quota)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    daily_movie_quota   = excluded.daily_movie_quota,
                    daily_episode_quota = excluded.daily_episode_quota
                """,
                (user_id, daily_movie_quota, daily_episode_quota),
            )
            conn.commit()
        finally:
            conn.close()
