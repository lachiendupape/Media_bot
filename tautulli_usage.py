"""Weekly user usage summaries from Tautulli for welcome messages.

This module focuses on read-only analytics lookups and keeps network failures
non-fatal so users can still access the app when Tautulli is unavailable.
"""

import logging
import threading
import time
from collections import Counter

import requests

import config

log = logging.getLogger(__name__)


class _UsageCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, str | None]] = {}

    def get(self, key: str) -> str | None | object:
        if config.TAUTULLI_WELCOME_CACHE_SECONDS <= 0:
            return _CACHE_MISS
        with self._lock:
            entry = self._entries.get(key)
            if not entry:
                return _CACHE_MISS
            ts, value = entry
            if time.time() - ts > config.TAUTULLI_WELCOME_CACHE_SECONDS:
                self._entries.pop(key, None)
                return _CACHE_MISS
            return value

    def set(self, key: str, value: str | None) -> None:
        if config.TAUTULLI_WELCOME_CACHE_SECONDS <= 0:
            return
        with self._lock:
            self._entries[key] = (time.time(), value)


_CACHE_MISS = object()
_cache = _UsageCache()


def _call_tautulli(cmd: str, **params) -> dict | None:
    if not config.TAUTULLI_URL or not config.TAUTULLI_API_KEY:
        return None

    url = config.TAUTULLI_URL.rstrip('/') + '/api/v2'
    query = {
        'apikey': config.TAUTULLI_API_KEY,
        'cmd': cmd,
    }
    query.update(params)

    try:
        response = requests.get(url, params=query, timeout=8)
        response.raise_for_status()
        data = response.json()
    except Exception:
        log.exception('tautulli.request_failed', extra={'cmd': cmd})
        return None

    payload = data.get('response') or {}
    if payload.get('result') != 'success':
        log.warning(
            'tautulli.request_non_success',
            extra={'cmd': cmd, 'message': payload.get('message')},
        )
        return None

    return payload.get('data') or {}


def _find_tautulli_user_id(plex_username: str) -> str | None:
    users = _call_tautulli('get_users')
    if not users:
        return None

    data = users.get('data')
    rows = data if isinstance(data, list) else users
    if not isinstance(rows, list):
        return None

    wanted = (plex_username or '').strip().lower()
    if not wanted:
        return None

    for row in rows:
        if not isinstance(row, dict):
            continue
        username = str(row.get('username', '')).strip().lower()
        friendly_name = str(row.get('friendly_name', '')).strip().lower()
        if username == wanted or friendly_name == wanted:
            user_id = row.get('user_id')
            return str(user_id) if user_id is not None else None

    return None


def _load_recent_history(user_id: str, length: int = 500) -> list[dict]:
    history = _call_tautulli('get_history', user_id=user_id, length=length)
    if not history:
        return []

    rows = history.get('data')
    if not isinstance(rows, list):
        return []

    return [row for row in rows if isinstance(row, dict)]


def _format_weekly_summary(
    username: str,
    history_rows: list[dict],
    now_ts: int | None = None,
) -> str | None:
    if not history_rows:
        return None

    now = int(now_ts if now_ts is not None else time.time())
    lookback_seconds = max(1, config.TAUTULLI_WELCOME_DAYS) * 24 * 3600
    cutoff = now - lookback_seconds

    episodes = 0
    movies = 0
    top_shows: Counter[str] = Counter()

    for row in history_rows:
        if not isinstance(row, dict):
            continue

        try:
            watched_at = int(row.get('date') or 0)
        except (TypeError, ValueError):
            watched_at = 0

        if watched_at < cutoff:
            continue

        media_type = str(row.get('media_type', '')).lower()
        if media_type == 'episode':
            episodes += 1
            show_name = (
                str(row.get('grandparent_title') or '').strip()
                or str(row.get('parent_title') or '').strip()
                or str(row.get('title') or '').strip()
            )
            if show_name:
                top_shows[show_name] += 1
        elif media_type == 'movie':
            movies += 1

    if episodes == 0 and movies == 0:
        return None

    top_n = max(1, config.TAUTULLI_WELCOME_TOP_SHOWS)
    top_show_lines: list[str] = []
    for name, count in top_shows.most_common(top_n):
        suffix = 'episode' if count == 1 else 'episodes'
        top_show_lines.append(f"- {name}: {count} {suffix}")

    pace = 'busy' if (episodes + movies) >= 5 else 'steady'
    episode_word = 'episode' if episodes == 1 else 'episodes'
    movie_word = 'movie' if movies == 1 else 'movies'
    days = max(1, config.TAUTULLI_WELCOME_DAYS)

    message_lines = [
        f"Hey {username}, this week you've been {pace}.",
        f"In the last {days} days you watched {episodes} {episode_word} and {movies} {movie_word}.",
    ]

    if top_show_lines:
        message_lines.append('Your top shows this week were:')
        message_lines.extend(top_show_lines)

    return "\n".join(message_lines)


def build_weekly_usage_message(plex_username: str) -> str | None:
    """Return a weekly usage summary for the given Plex username.

    Returns None when disabled, unavailable, or no recent activity exists.
    """
    if not config.TAUTULLI_WELCOME_ENABLED:
        return None

    username = (plex_username or '').strip()
    if not username:
        return None

    cache_key = username.lower()
    cached = _cache.get(cache_key)
    if cached is not _CACHE_MISS:
        return cached

    user_id = _find_tautulli_user_id(username)
    if not user_id:
        _cache.set(cache_key, None)
        return None

    history_rows = _load_recent_history(user_id)
    message = _format_weekly_summary(username, history_rows)
    _cache.set(cache_key, message)
    return message


def build_phase2_series_followup_hint() -> str:
    """Return a short placeholder text for Phase 2 roadmap.

    This is intentionally not wired into UI yet; it keeps the next phase scoped
    and discoverable in code.
    """
    return (
        'Phase 2 idea: detect when a user finishes a series and offer to queue '
        'the next season automatically.'
    )
