from stella.moderation import inspect_links, sanitize_reason


def test_link_inspection_marks_embedded_credentials():
    result = inspect_links("mira https://user:pass@example.com/path")
    assert result and result[0]["risk"] == "high"


def test_link_inspection_does_not_call_link_safe():
    result = inspect_links("https://example.com")
    assert result and result[0]["risk"] == "low"
    assert "no garantiza" in result[0]["reason"]


def test_moderation_reason_is_bounded():
    assert len(sanitize_reason("x" * 500)) == 240
