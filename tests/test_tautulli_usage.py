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
    assert 'Welcome back, alex' in message.text
    assert 'watched 3 episodes and 1 movie' in message.text
    assert '- Bluey: 2 episodes' in message.text
    assert '- Severance: 1 episode' in message.text


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
    assert 'Up next:' in message.text
    assert 'finished Bluey Season 1' in message.text
    assert len(message.suggestions) == 1
    assert message.suggestions[0].show == 'Bluey'
    assert message.suggestions[0].completed_season == 1
    assert message.suggestions[0].next_season == 2


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
    assert 'Up next:' not in message.text
    assert message.suggestions == []


# ---------------------------------------------------------------------------
# get_all_watchers_for_title tests
# ---------------------------------------------------------------------------

def _make_users_response(users):
    """Return a fake get_users Tautulli response list."""
    return users


def _make_history_response(rows):
    """Return a fake get_history Tautulli response dict."""
    return {'data': rows}


def test_get_all_watchers_returns_empty_when_tautulli_unconfigured(monkeypatch):
    """Returns {} immediately when TAUTULLI_URL or TAUTULLI_API_KEY is missing."""
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_URL', '')
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_API_KEY', 'key')

    result = tautulli_usage.get_all_watchers_for_title('Dune')
    assert result == {}


def test_get_all_watchers_returns_empty_when_api_key_missing(monkeypatch):
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_URL', 'http://tautulli')
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_API_KEY', '')

    result = tautulli_usage.get_all_watchers_for_title('Dune')
    assert result == {}


def test_get_all_watchers_movie_match(monkeypatch):
    """Counts a movie watch for the matching user; excludes non-matching titles."""
    now = int(time.time())
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_URL', 'http://tautulli')
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_API_KEY', 'key')

    users = [
        {'user_id': '1', 'friendly_name': 'alice'},
        {'user_id': '2', 'friendly_name': 'bob'},
    ]
    # alice watched 'Dune' once, 'Inception' once; bob watched nothing matching
    history_by_user = {
        '1': [
            {'date': now - 100, 'media_type': 'movie', 'title': 'Dune'},
            {'date': now - 200, 'media_type': 'movie', 'title': 'Inception'},
        ],
        '2': [
            {'date': now - 100, 'media_type': 'movie', 'title': 'Inception'},
        ],
    }

    def fake_call(cmd, **params):
        if cmd == 'get_users':
            return _make_users_response(users)
        if cmd == 'get_history':
            uid = str(params.get('user_id', ''))
            rows = history_by_user.get(uid, [])
            return _make_history_response(rows)
        return None

    monkeypatch.setattr(tautulli_usage, '_call_tautulli', fake_call)

    result = tautulli_usage.get_all_watchers_for_title('Dune', days=7)
    assert result == {'alice': 1}


def test_get_all_watchers_movie_case_insensitive(monkeypatch):
    """Title matching is case-insensitive."""
    now = int(time.time())
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_URL', 'http://tautulli')
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_API_KEY', 'key')

    users = [{'user_id': '1', 'friendly_name': 'alice'}]
    history = [{'date': now - 100, 'media_type': 'movie', 'title': 'DUNE'}]

    def fake_call(cmd, **params):
        if cmd == 'get_users':
            return users
        return {'data': history if params.get('user_id') == '1' else []}

    monkeypatch.setattr(tautulli_usage, '_call_tautulli', fake_call)

    result = tautulli_usage.get_all_watchers_for_title('dune', days=7)
    assert result == {'alice': 1}


def test_get_all_watchers_tv_season_match(monkeypatch):
    """Counts distinct episodes per user for the correct show + season."""
    now = int(time.time())
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_URL', 'http://tautulli')
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_API_KEY', 'key')

    users = [
        {'user_id': '1', 'friendly_name': 'alice'},
        {'user_id': '2', 'friendly_name': 'bob'},
    ]
    history_by_user = {
        # alice: 3 distinct S1 episodes + 1 S2 episode (should not count)
        '1': [
            {'date': now - 10, 'media_type': 'episode', 'grandparent_title': 'Bluey', 'parent_media_index': 1, 'media_index': 1},
            {'date': now - 20, 'media_type': 'episode', 'grandparent_title': 'Bluey', 'parent_media_index': 1, 'media_index': 2},
            {'date': now - 30, 'media_type': 'episode', 'grandparent_title': 'Bluey', 'parent_media_index': 1, 'media_index': 3},
            {'date': now - 40, 'media_type': 'episode', 'grandparent_title': 'Bluey', 'parent_media_index': 2, 'media_index': 1},
        ],
        # bob: watched a different show, should not appear
        '2': [
            {'date': now - 10, 'media_type': 'episode', 'grandparent_title': 'Severance', 'parent_media_index': 1, 'media_index': 1},
        ],
    }

    def fake_call(cmd, **params):
        if cmd == 'get_users':
            return users
        uid = str(params.get('user_id', ''))
        return {'data': history_by_user.get(uid, [])}

    monkeypatch.setattr(tautulli_usage, '_call_tautulli', fake_call)

    result = tautulli_usage.get_all_watchers_for_title('Bluey', season_number=1, days=7)
    assert result == {'alice': 3}


def test_get_all_watchers_tv_season_filtering(monkeypatch):
    """Season filtering excludes episodes from other seasons."""
    now = int(time.time())
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_URL', 'http://tautulli')
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_API_KEY', 'key')

    users = [{'user_id': '1', 'friendly_name': 'alice'}]
    # Only S2 episodes — should be excluded when querying for S1
    history = [
        {'date': now - 10, 'media_type': 'episode', 'grandparent_title': 'Bluey', 'parent_media_index': 2, 'media_index': 1},
        {'date': now - 20, 'media_type': 'episode', 'grandparent_title': 'Bluey', 'parent_media_index': 2, 'media_index': 2},
    ]

    def fake_call(cmd, **params):
        if cmd == 'get_users':
            return users
        return {'data': history if params.get('user_id') == '1' else []}

    monkeypatch.setattr(tautulli_usage, '_call_tautulli', fake_call)

    result = tautulli_usage.get_all_watchers_for_title('Bluey', season_number=1, days=7)
    assert result == {}


def test_get_all_watchers_cutoff_filters_old_rows(monkeypatch):
    """Rows older than the days cutoff are excluded from the count."""
    now = int(time.time())
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_URL', 'http://tautulli')
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_API_KEY', 'key')

    users = [{'user_id': '1', 'friendly_name': 'alice'}]
    # One recent row, one beyond the 7-day cutoff
    history = [
        {'date': now - 100, 'media_type': 'movie', 'title': 'Dune'},
        {'date': now - 10 * 24 * 3600, 'media_type': 'movie', 'title': 'Dune'},
    ]

    def fake_call(cmd, **params):
        if cmd == 'get_users':
            return users
        return {'data': history if params.get('user_id') == '1' else []}

    monkeypatch.setattr(tautulli_usage, '_call_tautulli', fake_call)

    result = tautulli_usage.get_all_watchers_for_title('Dune', days=7)
    # Only the recent watch should count; 'movie' de-duplication tracks 'movie'
    # sentinel so the count is 1 regardless of how many recent rows match.
    assert result == {'alice': 1}


def test_get_all_watchers_pagination_fetches_multiple_pages(monkeypatch):
    """Pagination continues until a page shorter than page_len is returned."""
    now = int(time.time())
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_URL', 'http://tautulli')
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_API_KEY', 'key')

    users = [{'user_id': '1', 'friendly_name': 'alice'}]

    # Simulate two full pages (500 rows each) and one partial page
    page_len = 500
    full_page_movies = [
        {'date': now - 10, 'media_type': 'movie', 'title': 'Dune'}
        for _ in range(page_len)
    ]
    partial_page = [
        {'date': now - 20, 'media_type': 'movie', 'title': 'Dune'}
        for _ in range(3)
    ]

    call_log: list[int] = []

    def fake_call(cmd, **params):
        if cmd == 'get_users':
            return users
        start = params.get('start', 0)
        call_log.append(start)
        if start == 0:
            return {'data': full_page_movies}
        if start == page_len:
            return {'data': partial_page}
        return {'data': []}

    monkeypatch.setattr(tautulli_usage, '_call_tautulli', fake_call)

    result = tautulli_usage.get_all_watchers_for_title('Dune', days=7)
    # Movie de-duplication: 'movie' sentinel used, so count is 1
    assert result == {'alice': 1}
    # Verify pagination: pages at start=0 and start=500 were fetched
    assert 0 in call_log
    assert page_len in call_log
    # No third page should be requested after a partial page
    assert page_len * 2 not in call_log


def test_get_all_watchers_pagination_stops_on_empty_page(monkeypatch):
    """Pagination stops when a page returns no rows."""
    now = int(time.time())
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_URL', 'http://tautulli')
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_API_KEY', 'key')

    users = [{'user_id': '1', 'friendly_name': 'alice'}]
    page_len = 500
    full_page = [
        {'date': now - 10, 'media_type': 'movie', 'title': 'Dune'}
        for _ in range(page_len)
    ]

    call_count = {'n': 0}

    def fake_call(cmd, **params):
        if cmd == 'get_users':
            return users
        call_count['n'] += 1
        start = params.get('start', 0)
        if start == 0:
            return {'data': full_page}
        return {'data': []}  # empty page stops pagination

    monkeypatch.setattr(tautulli_usage, '_call_tautulli', fake_call)

    result = tautulli_usage.get_all_watchers_for_title('Dune', days=7)
    assert result == {'alice': 1}
    # Should have fetched page at start=0 and stopped after the empty page
    assert call_count['n'] == 2


def test_get_all_watchers_returns_empty_on_no_matches(monkeypatch):
    """Returns empty dict when no history rows match the title."""
    now = int(time.time())
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_URL', 'http://tautulli')
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_API_KEY', 'key')

    users = [{'user_id': '1', 'friendly_name': 'alice'}]
    history = [{'date': now - 10, 'media_type': 'movie', 'title': 'Inception'}]

    def fake_call(cmd, **params):
        if cmd == 'get_users':
            return users
        return {'data': history if params.get('user_id') == '1' else []}

    monkeypatch.setattr(tautulli_usage, '_call_tautulli', fake_call)

    result = tautulli_usage.get_all_watchers_for_title('Dune', days=7)
    assert result == {}


def test_get_all_watchers_multiple_users(monkeypatch):
    """Counts are aggregated independently per user."""
    now = int(time.time())
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_URL', 'http://tautulli')
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_API_KEY', 'key')

    users = [
        {'user_id': '1', 'friendly_name': 'alice'},
        {'user_id': '2', 'friendly_name': 'bob'},
    ]
    history_by_user = {
        '1': [
            {'date': now - 10, 'media_type': 'episode', 'grandparent_title': 'Bluey', 'parent_media_index': 1, 'media_index': 1},
            {'date': now - 20, 'media_type': 'episode', 'grandparent_title': 'Bluey', 'parent_media_index': 1, 'media_index': 2},
        ],
        '2': [
            {'date': now - 15, 'media_type': 'episode', 'grandparent_title': 'Bluey', 'parent_media_index': 1, 'media_index': 1},
        ],
    }

    def fake_call(cmd, **params):
        if cmd == 'get_users':
            return users
        uid = str(params.get('user_id', ''))
        return {'data': history_by_user.get(uid, [])}

    monkeypatch.setattr(tautulli_usage, '_call_tautulli', fake_call)

    result = tautulli_usage.get_all_watchers_for_title('Bluey', season_number=1, days=7)
    assert result == {'alice': 2, 'bob': 1}


def test_get_all_watchers_episode_deduplication(monkeypatch):
    """The same episode watched twice by the same user counts only once."""
    now = int(time.time())
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_URL', 'http://tautulli')
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_API_KEY', 'key')

    users = [{'user_id': '1', 'friendly_name': 'alice'}]
    history = [
        {'date': now - 10, 'media_type': 'episode', 'grandparent_title': 'Bluey', 'parent_media_index': 1, 'media_index': 1},
        {'date': now - 20, 'media_type': 'episode', 'grandparent_title': 'Bluey', 'parent_media_index': 1, 'media_index': 1},
    ]

    def fake_call(cmd, **params):
        if cmd == 'get_users':
            return users
        return {'data': history if params.get('user_id') == '1' else []}

    monkeypatch.setattr(tautulli_usage, '_call_tautulli', fake_call)

    result = tautulli_usage.get_all_watchers_for_title('Bluey', season_number=1, days=7)
    assert result == {'alice': 1}


def test_get_all_watchers_returns_empty_when_get_users_fails(monkeypatch):
    """Returns {} gracefully when the get_users API call fails."""
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_URL', 'http://tautulli')
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_API_KEY', 'key')

    def fake_call(cmd, **params):
        return None  # simulate API failure

    monkeypatch.setattr(tautulli_usage, '_call_tautulli', fake_call)

    result = tautulli_usage.get_all_watchers_for_title('Dune', days=7)
    assert result == {}


def test_get_all_watchers_tv_uses_grandparent_title_fallback(monkeypatch):
    """Falls back to parent_title then title when grandparent_title is absent."""
    now = int(time.time())
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_URL', 'http://tautulli')
    monkeypatch.setattr(tautulli_usage.config, 'TAUTULLI_API_KEY', 'key')

    users = [{'user_id': '1', 'friendly_name': 'alice'}]
    # Row uses parent_title instead of grandparent_title
    history = [
        {'date': now - 10, 'media_type': 'episode', 'grandparent_title': '', 'parent_title': 'Bluey', 'parent_media_index': 1, 'media_index': 1},
    ]

    def fake_call(cmd, **params):
        if cmd == 'get_users':
            return users
        return {'data': history if params.get('user_id') == '1' else []}

    monkeypatch.setattr(tautulli_usage, '_call_tautulli', fake_call)

    result = tautulli_usage.get_all_watchers_for_title('Bluey', season_number=1, days=7)
    assert result == {'alice': 1}
