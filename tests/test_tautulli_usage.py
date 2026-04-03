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
    assert 'Hey alex' in message
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
