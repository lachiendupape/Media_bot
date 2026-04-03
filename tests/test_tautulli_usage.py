import time

import tautulli_usage


def test_format_weekly_summary_counts_and_top_shows(monkeypatch):
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_WELCOME_DAYS', 7)
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_WELCOME_TOP_SHOWS', 2)

    now = int(time.time())
    rows = [
        {'date': now - 100, 'media_type': 'episode', 'grandparent_title': 'Bluey'},
        {'date': now - 120, 'media_type': 'episode', 'grandparent_title': 'Bluey'},
        {'date': now - 140, 'media_type': 'episode', 'grandparent_title': 'Severance'},
        {'date': now - 160, 'media_type': 'movie', 'title': 'Dune'},
        {'date': now - 8 * 24 * 3600, 'media_type': 'movie', 'title': 'Old Movie'},
    ]

    message = tautulli_usage._format_weekly_summary('alex', rows, now_ts=now)

    assert message is not None
    assert 'Welcome back, alex' in message
    assert 'watched 3 episodes and 1 movie' in message
    assert '- Bluey: 2 episodes' in message
    assert '- Severance: 1 episode' in message


def test_format_weekly_summary_returns_none_with_no_recent_activity(monkeypatch):
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_WELCOME_DAYS', 7)
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_WELCOME_TOP_SHOWS', 3)

    now = int(time.time())
    rows = [
        {'date': now - 15 * 24 * 3600, 'media_type': 'episode', 'grandparent_title': 'Bluey'},
        {'date': now - 15 * 24 * 3600, 'media_type': 'movie', 'title': 'Dune'},
    ]

    message = tautulli_usage._format_weekly_summary('alex', rows, now_ts=now)

    assert message is None


class _FakeSonarr:
    def __init__(self, series_by_title):
        self.series_by_title = series_by_title

    def find_series_in_library(self, title):
        payload = self.series_by_title.get(title, None)
        if payload is None:
            return []
        return [payload]


def test_phase2_suggests_next_season_when_completion_threshold_met(monkeypatch):
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_WELCOME_DAYS', 7)
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_WELCOME_TOP_SHOWS', 3)
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_PHASE2_ENABLED', True)
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_PHASE2_MIN_COMPLETION_RATIO', 0.9)
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_PHASE2_MAX_SUGGESTIONS', 1)

    series_payload = {
        'title': 'Bluey',
        'seasons': [
            {'seasonNumber': 1, 'statistics': {'episodeCount': 10}},
            {'seasonNumber': 2, 'statistics': {'episodeCount': 10}},
        ],
    }
    monkeypatch.setattr(
        tautulli_usage,
        '_get_sonarr_client',
        lambda: _FakeSonarr({'Bluey': series_payload}),
    )

    now = int(time.time())
    rows = [
        {
            'date': now - 100 - i,
            'media_type': 'episode',
            'grandparent_title': 'Bluey',
            'parent_media_index': 1,
            'media_index': i + 1,
        }
        for i in range(10)
    ]

    message = tautulli_usage._format_weekly_summary('alex', rows, now_ts=now)

    assert message is not None
    assert 'Up next:' in message
    assert 'Want me to queue Season 2?' in message


def test_phase2_skips_suggestion_when_next_season_missing(monkeypatch):
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_WELCOME_DAYS', 7)
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_WELCOME_TOP_SHOWS', 3)
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_PHASE2_ENABLED', True)
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_PHASE2_MIN_COMPLETION_RATIO', 0.9)
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_PHASE2_MAX_SUGGESTIONS', 1)

    series_payload = {
        'title': 'Bluey',
        'seasons': [
            {'seasonNumber': 1, 'statistics': {'episodeCount': 10}},
        ],
    }
    monkeypatch.setattr(
        tautulli_usage,
        '_get_sonarr_client',
        lambda: _FakeSonarr({'Bluey': series_payload}),
    )

    now = int(time.time())
    rows = [
        {
            'date': now - 100 - i,
            'media_type': 'episode',
            'grandparent_title': 'Bluey',
            'parent_media_index': 1,
            'media_index': i + 1,
        }
        for i in range(10)
    ]

    message = tautulli_usage._format_weekly_summary('alex', rows, now_ts=now)

    assert message is not None
    assert 'Up next:' not in message
