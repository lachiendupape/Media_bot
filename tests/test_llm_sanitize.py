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


# --- _is_selection_prompt ---

def test_is_selection_prompt_detects_numbered_list():
    text = (
        "I found multiple TV series matching 'Neighbours'. Which one would you like to add?\n"
        "1. Neighbours (1985)\n"
        "2. Neighbours (2022)\n"
        "Reply with the number (for example: 1)."
    )
    assert llm._is_selection_prompt(text) is True


def test_is_selection_prompt_detects_movie_disambiguation():
    text = (
        "I found multiple movies matching 'Neighbours'. Which version would you like to add?\n"
        "1. Neighbours (1994)\n"
        "2. Neighbours (2013)\n"
        "Reply with the number (for example: 1)."
    )
    assert llm._is_selection_prompt(text) is True


def test_is_selection_prompt_returns_false_for_plain_text():
    text = "Great news! 'Neighbours' Season 1 has been grabbed and is downloading now."
    assert llm._is_selection_prompt(text) is False


def test_is_selection_prompt_returns_false_for_season_question():
    text = (
        "'Neighbours (1985)' has 37 seasons: 1, 2, 3, 4, 5.\n"
        "Which season would you like to add?"
    )
    assert llm._is_selection_prompt(text) is False


# --- _apply_speaking_style bypasses selection prompts ---

def test_apply_speaking_style_skips_numbered_selection_list(monkeypatch):
    """_apply_speaking_style must return the original text unchanged when it
    contains a numbered selection list, to prevent the LLM from dropping items."""
    disambiguation = (
        "I found multiple TV series matching 'Neighbours'. Which one would you like to add?\n"
        "1. Neighbours (1985)\n"
        "2. Neighbours (2022)\n"
        "Reply with the number (for example: 1)."
    )
    # Patch client to ensure it is never called for selection prompts.
    called = []

    class _FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    called.append(kwargs)
                    raise AssertionError("LLM should not be called for selection prompts")

    monkeypatch.setattr(llm, "client", _FakeClient())

    result = llm._apply_speaking_style(disambiguation, "robot")

    assert result == disambiguation
    assert called == [], "LLM client should not have been invoked"


def test_apply_speaking_style_calls_llm_for_plain_text(monkeypatch):
    """_apply_speaking_style should call the LLM for plain (non-list) text."""
    plain = "Great news! 'Neighbours' Season 1 is downloading now."
    styled = "AFFIRMATIVE. DOWNLOADING NEIGHBOURS SEASON 1. TASK ACCEPTED."

    class _FakeChoice:
        class message:
            content = styled

    class _FakeCompletion:
        choices = [_FakeChoice()]

    class _FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return _FakeCompletion()

    monkeypatch.setattr(llm, "client", _FakeClient())

    result = llm._apply_speaking_style(plain, "robot")

    assert result == styled
