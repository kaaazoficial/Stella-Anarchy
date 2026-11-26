import pytest

from stella.bot import classify_playful_stella_message


@pytest.mark.parametrize("message", [
    "Stella, you're a bitch",
    "Stella, you are a fucking idiot",
    "Stella, vai se foder",
    "Stella, você é um idiota",
    "Stella, tu es une connasse",
    "Stella, du bist ein Arschloch",
    "Stella, sei uno stronzo",
    "Stella, ты идиот",
    "Stella, أنت غبي",
    "Stella, 你是个傻逼",
    "Stella, お前はバカ",
    "Stella, 너는 병신",
    "Stella, तू बेवकूफ है",
    "Stella, joder, you are stupid",
])
def test_direct_insults_are_detected_across_languages(message):
    assert classify_playful_stella_message(message) == "insult"


@pytest.mark.parametrize("message", [
    "Stella, idiot",
    "Stella, asshole",
    "Stella, connard",
    "Stella, stronzo",
    "Stella, дурак",
    "Stella, 傻逼",
])
def test_standalone_insults_are_detected_when_platform_confirms_address(message):
    body = message.split(",", 1)[1].strip()
    assert classify_playful_stella_message(body, explicitly_addressed=True) == "insult"


@pytest.mark.parametrize("message", [
    "Stella, what does bitch mean?",
    "Stella, ¿qué significa connard?",
    "Stella, translate the word idiot",
    "Stella, deja de hablarme así",
    "Stella, please leave me alone",
    "That asshole insulted my friend",
    "The movie character is an idiot",
])
def test_quotes_language_questions_boundaries_and_third_party_insults_are_not_attacks(message):
    explicitly_addressed = message.casefold().startswith("stella")
    assert classify_playful_stella_message(message, explicitly_addressed=explicitly_addressed) is None
