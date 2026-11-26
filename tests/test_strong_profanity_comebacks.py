import pytest

from stella.bot import classify_playful_stella_message


@pytest.mark.parametrize("message", [
    "Stella, malparida",
    "Stella, eres una malparida",
    "Stella, hija de puta",
    "Stella, eres una hija de puta",
    "Stella, gonorrea",
    "Stella, pedazo de mierda",
    "Stella, carechimba",
    "Stella, eres una perra",
    "Stella, hdp",
    "Stella, hpt",
])
def test_strong_direct_profanity_is_detected_as_an_insult(message):
    assert classify_playful_stella_message(message) == "insult"


@pytest.mark.parametrize("message", [
    "Qué malparida la situación",
    "Mi personaje dice 'hija de puta' en la novela",
    "¿Qué significa gonorrea como insulto?",
    "Ese tipo es un idiota",
    "Ese personaje es una gonorrea",
    "Qué malparida la situación",
])
def test_profanity_not_directed_at_stella_is_not_routed_as_an_insult(message):
    assert classify_playful_stella_message(message) is None



def test_directed_standalone_insults_are_recognized_after_chat_formatting():
    for value in ("[ci]putita", "[c]imbécil", "[cb]weona", "gilipollas", "conchuda"):
        assert classify_playful_stella_message(
            value, explicitly_addressed=True
        ) == "insult"


def test_local_comebacks_are_strong_variable_and_do_not_assign_gender():
    import re
    from stella.bot import (
        _STELLA_INSULT_COMEBACKS,
        _STELLA_HUMILIATION_COMEBACKS,
        build_stella_comeback,
    )

    assert len(_STELLA_INSULT_COMEBACKS) >= 10
    assert len(_STELLA_HUMILIATION_COMEBACKS) >= 8
    assert any("mierda" in line.casefold() for line in _STELLA_INSULT_COMEBACKS)
    assert any("imbécil" in line.casefold() for line in _STELLA_INSULT_COMEBACKS)
    banned_gender = re.compile(
        r"\\b(?:él|ella|ellos|ellas|chico|chica|amigo|amiga|"
        r"señor|señora|malparido|malparida|idiota[o])\\b",
        re.I,
    )
    for line in _STELLA_INSULT_COMEBACKS + _STELLA_HUMILIATION_COMEBACKS:
        assert not banned_gender.search(line)
    for _ in range(40):
        assert build_stella_comeback("insult", "putita") in _STELLA_INSULT_COMEBACKS


def test_laughter_only_does_not_trigger_a_hostile_comeback():
    for value in ("jajaja", "JAJAJAJA", "xD", "😂🤣"):
        assert classify_playful_stella_message(value, explicitly_addressed=True) is None
