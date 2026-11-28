from stella.matching import suggest_command


def test_suggests_a_plausible_command_typo():
    assert suggest_command("ayudaa", {"ayuda", "ping"}) == ("ayuda", 91)


def test_rejects_a_low_confidence_command_match():
    assert suggest_command("aydu", {"ayuda", "ping"}) is None


def test_normalizes_prefix_and_case_before_suggesting():
    assert suggest_command("!AYUDAA", {"ayuda"}) == ("ayuda", 91)


def test_does_not_suggest_for_too_short_input():
    assert suggest_command("ay", {"ayuda", "ping"}) is None
