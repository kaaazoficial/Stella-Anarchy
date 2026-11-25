from stella.ai_core import (
    build_ai_instructions,
    clarification_for_unspecified_topic,
    is_contextual_followup_request,
    resolve_community_faq,
)


def test_conversation_instructions_prioritize_the_current_request():
    prompt = build_ai_instructions(frozenset(), "conversation")

    assert "exclusivamente la petición actual" in prompt
    assert "No cambies de tema" in prompt
    assert "No introduzcas personajes, aplicaciones, juegos" in prompt
    assert "haz una sola pregunta breve" in prompt
    assert "no ejecutes herramientas" in prompt


def test_conversation_instructions_do_not_expose_tools():
    prompt = build_ai_instructions(frozenset(), "conversation")

    assert "SIN HERRAMIENTAS." in prompt
    assert "JSON requerido: objeto con únicamente answer (texto)." in prompt


def test_explicit_topic_change_drops_previous_context():
    assert not is_contextual_followup_request("otra cosa, necesito ayuda con Python")
    assert not is_contextual_followup_request("nada que ver, cambiando de tema")
    assert not is_contextual_followup_request("por cierto, ¿qué hora es?")


def test_short_referential_followup_can_keep_context():
    assert is_contextual_followup_request("¿y cómo funciona?")
    assert is_contextual_followup_request("¿qué características tiene ella?")


def test_unspecified_help_request_asks_for_the_subject():
    answer = clarification_for_unspecified_topic("necesito ayuda")

    assert answer
    assert "¿Sobre qué tema" in answer


def test_faq_does_not_answer_unrelated_questions_with_a_generic_greeting():
    assert resolve_community_faq("hola") == "¡Hola! Soy Stella. ¿En qué puedo ayudarte?"
    assert resolve_community_faq("¿cómo reparo una placa electrónica?") is None
