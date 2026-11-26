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
])
def test_profanity_not_directed_at_stella_is_not_routed_as_an_insult(message):
    assert classify_playful_stella_message(message) is None
