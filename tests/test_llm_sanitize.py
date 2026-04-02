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
