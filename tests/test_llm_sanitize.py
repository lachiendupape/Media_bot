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
