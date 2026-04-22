"""Conversation memory management.

Stores bidirectional conversation history on a per-user basis using SQLite.
Supports optional TTL-based expiration to limit database growth.

Usage example::

    from memory import save_turn, load_prior_turns, delete_identity_all

    # After a user exchanges messages with the chat endpoint
    save_turn(identity="user123", role="user", content="What's new?")
    save_turn(identity="user123", role="assistant", content="Here's what's new...")

    # Before processing a new request, load prior turns
    prior_turns = load_prior_turns(identity="user123", max_turns=20)

    # On logout or explicit purge
    delete_identity_all(identity="user123")
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path

import config

log = logging.getLogger(__name__)

_DB_PATH = Path(config.DATA_DIR) / "memory.db"
_lock = threading.Lock()


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS conversation_turns (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            identity  TEXT    NOT NULL,
            role      TEXT    NOT NULL,
            content   TEXT    NOT NULL,
            created_at INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_ct_identity_created
            ON conversation_turns (identity, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_ct_identity
            ON conversation_turns (identity);
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


def save_turn(identity: str, role: str, content: str) -> None:
    """Save a single conversation turn (user message or assistant response).

    Args:
        identity: Unique identifier for the user/session
        role: "user" or "assistant"
        content: The message content
    """
    with _lock:
        conn = _get_connection()
        try:
            created_at = int(time.time())
            conn.execute(
                """
                INSERT INTO conversation_turns (identity, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (identity, role, content, created_at),
            )
            conn.commit()
        finally:
            conn.close()


def load_prior_turns(identity: str, max_turns: int = 20) -> list[dict]:
    """Load conversation history for a given identity.

    Args:
        identity: Unique identifier for the user/session
        max_turns: Maximum number of stored turns to retrieve

    Returns:
        List of dicts with keys: id, identity, role, content, created_at
    """
    if max_turns <= 0:
        return []

    with _lock:
        conn = _get_connection()
        try:
            rows = conn.execute(
                """
                SELECT id, identity, role, content, created_at
                FROM conversation_turns
                WHERE identity = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (identity, max_turns),
            ).fetchall()

            # Reverse to get chronological order (oldest first)
            return [dict(row) for row in reversed(rows)]
        finally:
            conn.close()


def trim_to_n(identity: str, max_turns: int = 20) -> None:
    """Keep only the most recent N turns for a given identity.

    Older turns beyond the limit are deleted.

    Args:
        identity: Unique identifier for the user/session
        max_turns: Maximum number of turns to retain
    """
    if max_turns <= 0:
        delete_identity_all(identity)
        return

    with _lock:
        conn = _get_connection()
        try:
            conn.execute(
                """
                DELETE FROM conversation_turns
                WHERE identity = ?
                  AND id NOT IN (
                    SELECT id FROM (
                        SELECT id
                        FROM conversation_turns
                        WHERE identity = ?
                        ORDER BY created_at DESC, id DESC
                        LIMIT ?
                    )
                  )
                """,
                (identity, identity, max_turns),
            )
            conn.commit()
        finally:
            conn.close()


def cleanup_expired_ttl(ttl_seconds: int = 86400) -> None:
    """Delete conversation turns older than the TTL.

    Args:
        ttl_seconds: Time-to-live in seconds (default 24 hours)
    """
    with _lock:
        conn = _get_connection()
        try:
            cutoff_time = int(time.time()) - ttl_seconds
            conn.execute(
                "DELETE FROM conversation_turns WHERE created_at < ?",
                (cutoff_time,),
            )
            conn.commit()
        finally:
            conn.close()


def delete_identity_all(identity: str) -> None:
    """Delete all conversation turns for a given identity.

    Used on logout or when user requests data purge.

    Args:
        identity: Unique identifier for the user/session
    """
    with _lock:
        conn = _get_connection()
        try:
            conn.execute("DELETE FROM conversation_turns WHERE identity = ?", (identity,))
            conn.commit()
        finally:
            conn.close()


def get_stats() -> dict:
    """Return database statistics (total turns, identities, oldest/newest timestamps)."""
    with _lock:
        conn = _get_connection()
        try:
            total_turns = conn.execute(
                "SELECT COUNT(*) as count FROM conversation_turns"
            ).fetchone()["count"]

            unique_identities = conn.execute(
                "SELECT COUNT(DISTINCT identity) as count FROM conversation_turns"
            ).fetchone()["count"]

            timestamps = conn.execute(
                """
                SELECT MIN(created_at) as oldest, MAX(created_at) as newest
                FROM conversation_turns
                """
            ).fetchone()

            return {
                "total_turns": total_turns,
                "unique_identities": unique_identities,
                "oldest_timestamp": timestamps["oldest"],
                "newest_timestamp": timestamps["newest"],
            }
        finally:
            conn.close()
