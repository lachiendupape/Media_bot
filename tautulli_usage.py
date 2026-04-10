"""Weekly user usage summaries from Tautulli for welcome messages.

This module focuses on read-only analytics lookups and keeps network failures
non-fatal so users can still access the app when Tautulli is unavailable.
"""

import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import NamedTuple

import requests

import config


@dataclass
class SeasonSuggestion:
    """A next-season suggestion derived from watch history."""
    show: str
    completed_season: int
    next_season: int


@dataclass
class WelcomeData:
    """Structured result from build_weekly_usage_message."""
    text: str
    suggestions: list[SeasonSuggestion] = field(default_factory=list)

log = logging.getLogger(__name__)


class _UsageCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, WelcomeData | None]] = {}

    def get(self, key: str) -> WelcomeData | None | object:
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

    def set(self, key: str, value: WelcomeData | None) -> None:
        if config.TAUTULLI_WELCOME_CACHE_SECONDS <= 0:
            return
        with self._lock:
            self._entries[key] = (time.time(), value)


_CACHE_MISS = object()
_cache = _UsageCache()


def _call_tautulli(cmd: str, **params) -> dict | list | None:
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


def _to_positive_int(value) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _get_sonarr_client():
    if not config.SONARR_URL or not config.SONARR_API_KEY:
        return None
    try:
        from api.sonarr import SonarrAPI

        return SonarrAPI()
    except Exception:
        log.exception('sonarr.client_init_failed')
        return None


def _season_episode_total(season: dict) -> int:
    stats = season.get('statistics') if isinstance(season, dict) else None
    if not isinstance(stats, dict):
        return 0

    candidates = (
        stats.get('episodeCount'),
        stats.get('totalEpisodeCount'),
        stats.get('episodeFileCount'),
    )
    for value in candidates:
        parsed = _to_positive_int(value)
        if parsed is not None:
            return parsed
    return 0


def _build_phase2_suggestions(history_rows: list[dict], cutoff: int) -> list[SeasonSuggestion]:
    if not config.TAUTULLI_PHASE2_ENABLED:
        return []

    sonarr = _get_sonarr_client()
    if sonarr is None:
        return []

    # watched[show][season] = set(unique episode numbers watched in lookback window)
    watched: dict[str, dict[int, set[int]]] = {}

    for row in history_rows:
        if not isinstance(row, dict):
            continue

        try:
            watched_at = int(row.get('date') or 0)
        except (TypeError, ValueError):
            watched_at = 0
        if watched_at < cutoff:
            continue

        if str(row.get('media_type', '')).lower() != 'episode':
            continue

        show_name = (
            str(row.get('grandparent_title') or '').strip()
            or str(row.get('parent_title') or '').strip()
            or str(row.get('title') or '').strip()
        )
        season_number = _to_positive_int(row.get('parent_media_index'))
        episode_number = _to_positive_int(row.get('media_index'))
        if not show_name or season_number is None or episode_number is None:
            continue

        by_season = watched.setdefault(show_name, {})
        by_season.setdefault(season_number, set()).add(episode_number)

    if not watched:
        return []

    min_ratio = max(0.5, min(1.0, float(config.TAUTULLI_PHASE2_MIN_COMPLETION_RATIO)))
    max_suggestions = max(0, int(config.TAUTULLI_PHASE2_MAX_SUGGESTIONS))
    if max_suggestions == 0:
        return []

    suggestions: list[str] = []
    for show_name, by_season in watched.items():
        if len(suggestions) >= max_suggestions:
            break

        try:
            matches = sonarr.find_series_in_library(show_name) or []
        except Exception:
            log.exception('sonarr.series_lookup_failed', extra={'show_name': show_name})
            continue
        if not matches:
            continue

        show_key = show_name.strip().lower()
        series = next(
            (s for s in matches if str(s.get('title', '')).strip().lower() == show_key),
            matches[0],
        )

        seasons = series.get('seasons')
        if not isinstance(seasons, list):
            continue
        season_map = {
            s.get('seasonNumber'): s
            for s in seasons
            if isinstance(s, dict) and _to_positive_int(s.get('seasonNumber')) is not None
        }

        completed_season = None
        for season_number, episode_set in sorted(by_season.items(), reverse=True):
            season_payload = season_map.get(season_number)
            if not season_payload:
                continue
            total_episodes = _season_episode_total(season_payload)
            if total_episodes <= 0:
                continue
            completion = len(episode_set) / total_episodes
            if completion >= min_ratio:
                completed_season = season_number
                break

        if completed_season is None:
            continue

        next_season_number = completed_season + 1
        next_season = season_map.get(next_season_number)
        if not next_season:
            continue
        if _season_episode_total(next_season) <= 0:
            continue

        suggestions.append(SeasonSuggestion(
            show=show_name,
            completed_season=completed_season,
            next_season=next_season_number,
        ))

    return suggestions


def _find_tautulli_user_id(plex_username: str) -> str | None:
    users = _call_tautulli('get_users')
    if not users:
        return None

    rows = users if isinstance(users, list) else users.get('data', [])
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

    rows = history if isinstance(history, list) else history.get('data')
    if not isinstance(rows, list):
        return []

    return [row for row in rows if isinstance(row, dict)]


def _format_weekly_summary(
    username: str,
    history_rows: list[dict],
    now_ts: int | None = None,
) -> WelcomeData | None:
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

    phase2_suggestions = _build_phase2_suggestions(history_rows, cutoff)

    top_n = max(1, config.TAUTULLI_WELCOME_TOP_SHOWS)
    top_show_lines: list[str] = []
    for name, count in top_shows.most_common(top_n):
        suffix = 'episode' if count == 1 else 'episodes'
        top_show_lines.append(f"- {name}: {count} {suffix}")

    episode_word = 'episode' if episodes == 1 else 'episodes'
    movie_word = 'movie' if movies == 1 else 'movies'
    days = max(1, config.TAUTULLI_WELCOME_DAYS)

    if episodes > 0 and movies > 0:
        stats = f"{episodes} {episode_word} and {movies} {movie_word}"
    elif episodes > 0:
        stats = f"{episodes} {episode_word}"
    else:
        stats = f"{movies} {movie_word}"

    message_lines = [
        f"Welcome back, {username}. In the last {days} days you watched {stats}.",
    ]

    if top_show_lines:
        message_lines.append('Top shows:')
        message_lines.extend(top_show_lines)

    if phase2_suggestions:
        message_lines.append('Up next:')
        for s in phase2_suggestions:
            message_lines.append(
                f"- You may have finished {s.show} Season {s.completed_season}."
            )

    return WelcomeData(text="\n".join(message_lines), suggestions=phase2_suggestions)


def build_weekly_usage_message(plex_username: str) -> WelcomeData | None:
    """Return a weekly usage summary for the given Plex username.

    Returns None when disabled, unavailable, or no recent activity exists.
    Returns a WelcomeData with .text and .suggestions otherwise.
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
