from stella.bot import SOCIAL_RESPONSE_GUIDANCE


def test_social_guidance_requires_semantic_detection_beyond_word_lists():
    guidance = SOCIAL_RESPONSE_GUIDANCE.casefold()
    assert "no dependas de una lista cerrada" in guidance
    assert "regionalismos" in guidance
    assert "errores ortográficos" in guidance
    assert "insinuaciones" in guidance
    assert "cualquier insulto" in guidance


def test_social_guidance_allows_multiple_responses_to_an_insult():
    guidance = SOCIAL_RESPONSE_GUIDANCE.casefold()
    assert "insulto explícito y fuerte" in guidance
    assert "sarcasmo" in guidance
    assert "marcarle el límite" in guidance
    assert "no estás obligada a insultar siempre" in guidance


def test_social_guidance_distinguishes_quotes_and_third_party_insults():
    guidance = SOCIAL_RESPONSE_GUIDANCE.casefold()
    assert "palabra ofensiva citada" in guidance
    assert "dirigida a otra persona" in guidance
