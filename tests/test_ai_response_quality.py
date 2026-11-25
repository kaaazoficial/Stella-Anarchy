from stella.ai_core import (
    build_ai_instructions,
    classify_request_intent,
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
    assert "No inventes relaciones entre la consulta y temas anteriores" in prompt


def test_conversation_instructions_do_not_expose_tools():
    prompt = build_ai_instructions(frozenset(), "conversation")

    assert "SIN HERRAMIENTAS." in prompt
    assert "JSON requerido: objeto con únicamente answer (texto)." in prompt
    assert "HERRAMIENTAS:" not in prompt


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


def test_intent_classifier_keeps_normal_questions_as_conversation():
    assert classify_request_intent("¿qué es la electricidad?") == "conversation"
    assert classify_request_intent("explícame cómo funciona un motor") == "conversation"


def test_intent_classifier_only_marks_explicit_actions_as_actions():
    assert classify_request_intent("calcula 25 por 4") == "action"
    assert classify_request_intent("investiga la historia de la electricidad") == "research"


def test_conversation_instructions_allow_real_chat_not_only_questions():
    prompt = build_ai_instructions(frozenset(), "conversation")

    assert "esta modalidad permite hablar con Stella" in prompt
    assert "charla, comenta, razona" in prompt
    assert "no hagas una pregunta de cierre por costumbre" in prompt


def test_context_keeps_a_natural_answer_to_stellas_specific_question():
    from stella.ai_core import StellaAICore

    history = [
        {"role": "user", "content": "Estoy pensando en estudiar informática."},
        {"role": "assistant", "content": "¿Qué es lo que más te llama la atención de esa carrera?"},
    ]

    assert StellaAICore.is_contextual_followup("Me gusta programar y crear juegos.", history)


def test_context_does_not_keep_history_after_an_explicit_topic_change():
    from stella.ai_core import StellaAICore

    history = [
        {"role": "user", "content": "Estoy pensando en estudiar informática."},
        {"role": "assistant", "content": "¿Qué es lo que más te llama la atención de esa carrera?"},
    ]

    assert not StellaAICore.is_contextual_followup(
        "Por cierto, necesito saber cómo funciona un motor eléctrico.", history
    )


def test_ai_context_is_ephemeral_bounded_and_isolated():
    from stella.ai_core import StellaAICore

    ai = StellaAICore(memory_ttl=600, memory_turns=5)
    ai._append_history("room:user-a", "user", "Tema privado de A")
    ai._append_history("room:user-a", "assistant", "Respuesta de A")
    ai._append_history("room:user-b", "user", "Tema privado de B")

    assert ai._history("room:user-a") == [
        {"role": "user", "content": "Tema privado de A"},
        {"role": "assistant", "content": "Respuesta de A"},
    ]
    assert ai._history("room:user-b") == [
        {"role": "user", "content": "Tema privado de B"},
    ]
    assert ai.memory_ttl == 600
    assert ai.memory_turns == 5

    ai.forget("room:user-a")
    assert ai._history("room:user-a") == []
    assert ai._history("room:user-b")
