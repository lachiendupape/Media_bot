import llm


def test_sanitize_direct_response_removes_json_code_block():
    raw = (
        "Checking media...\n\n"
        "```json\n"
        "{\n"
        "  \"status\": \"ok\",\n"
        "  \"result\": [{\"title\": \"Bluey\"}]\n"
        "}\n"
        "```\n\n"
        "There is a series titled 'Bluey' in your library."
    )

    cleaned, changed = llm._sanitize_direct_response_text(raw)

    assert changed is True
    assert "```json" not in cleaned
    assert '"status"' not in cleaned
    assert "There is a series titled 'Bluey' in your library." in cleaned


def test_sanitize_direct_response_keeps_normal_text_unchanged():
    raw = "There is a series titled 'Bluey' in your library."

    cleaned, changed = llm._sanitize_direct_response_text(raw)

    assert changed is False
    assert cleaned == raw


def test_sanitize_strips_cyrillic_preamble():
    """Leading paragraph with predominantly Cyrillic text is removed."""
    raw = (
        "Дмешта яс езикот на повече от една работноocrat娱乐平台登录网站oceflw5m\n\n"
        "Are you looking for movies or TV shows with Ryan Gosling?"
    )

    cleaned, changed = llm._sanitize_direct_response_text(raw)

    assert changed is True
    assert "Дмешта" not in cleaned
    assert "Are you looking for movies or TV shows with Ryan Gosling?" in cleaned


def test_sanitize_strips_language_tag_prefix():
    """Spurious 'word English:' tag is removed from the start of the English paragraph."""
    raw = (
        "Дмешта яс езикот娱乐平台\n\n"
        "widaemsag English: Are you looking for movies or TV shows with Ryan Gosling?"
    )

    cleaned, changed = llm._sanitize_direct_response_text(raw)

    assert changed is True
    assert "widaemsag" not in cleaned
    assert "Дмешта" not in cleaned
    assert "Are you looking for movies or TV shows with Ryan Gosling?" in cleaned


def test_sanitize_exact_issue_response():
    """Reproduce the exact response from the reported issue."""
    raw = (
        "Дмешта яс езикот на повече от една работноocrat娱乐平台登录网站oceflw5m\n\n"
        " widaemsag English: Are you looking for movies or TV shows with Ryan Gosling? "
        "Please specify if you want to search by actor, director, or both roles."
    )

    cleaned, changed = llm._sanitize_direct_response_text(raw)

    assert changed is True
    assert "Дмешта" not in cleaned
    assert "widaemsag" not in cleaned
    assert "Are you looking for movies or TV shows with Ryan Gosling?" in cleaned


def test_sanitize_keeps_english_with_accented_names():
    """English text that contains accented characters is not stripped."""
    raw = "François Truffaut directed many classic films."

    cleaned, changed = llm._sanitize_direct_response_text(raw)

    assert changed is False
    assert cleaned == raw


def test_strip_non_english_preamble_no_preamble():
    """Text with no non-English preamble is returned unchanged."""
    raw = "Looking up Ryan Gosling in your library."
    result = llm._strip_non_english_preamble(raw)
    assert result == raw


def test_requester_tag_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(llm.config, "ENABLE_REQUESTER_TAGGING", False)
    monkeypatch.setattr(llm.config, "REQUESTER_TAG_PREFIX", "")

    assert llm._requester_tag_from_username("Alice") is None


def test_requester_tag_sanitizes_username(monkeypatch):
    monkeypatch.setattr(llm.config, "ENABLE_REQUESTER_TAGGING", True)
    monkeypatch.setattr(llm.config, "REQUESTER_TAG_PREFIX", "")

    tag = llm._requester_tag_from_username("  A!li ce__123  ")

    assert tag == "a-li-ce__123"


def test_requester_tag_applies_prefix(monkeypatch):
    monkeypatch.setattr(llm.config, "ENABLE_REQUESTER_TAGGING", True)
    monkeypatch.setattr(llm.config, "REQUESTER_TAG_PREFIX", "Req ")

    tag = llm._requester_tag_from_username("Alice")

    assert tag == "req-alice"


def test_requester_tag_skips_placeholder_identities(monkeypatch):
    monkeypatch.setattr(llm.config, "ENABLE_REQUESTER_TAGGING", True)
    monkeypatch.setattr(llm.config, "REQUESTER_TAG_PREFIX", "")

    assert llm._requester_tag_from_username("unknown") is None
    assert llm._requester_tag_from_username("api_key") is None


def test_requester_tag_caps_length(monkeypatch):
    monkeypatch.setattr(llm.config, "ENABLE_REQUESTER_TAGGING", True)
    monkeypatch.setattr(llm.config, "REQUESTER_TAG_PREFIX", "req-")

    tag = llm._requester_tag_from_username("a" * 100)

    assert tag is not None
    assert len(tag) <= 64


def test_rule_based_route_handles_vague_download_completion_query(monkeypatch):
    expected = "mocked download status"
    telemetry = {}

    monkeypatch.setattr(llm, "check_download_status_handler", lambda: expected)

    result = llm._try_rule_based_route(
        "Can you tell me when it has completed downloading?",
        telemetry=telemetry,
    )

    assert result == expected
    assert telemetry["heuristic_route"] == "check_download_status"


def test_rule_based_route_does_not_treat_add_download_request_as_status(monkeypatch):
    called = False

    def _handler():
        nonlocal called
        called = True
        return "should not be called"

    monkeypatch.setattr(llm, "check_download_status_handler", _handler)

    result = llm._try_rule_based_route("Download Interstellar for me")

    assert result is None
    assert called is False
