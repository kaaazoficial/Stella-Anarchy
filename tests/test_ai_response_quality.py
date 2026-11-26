import pytest

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


def test_stella_identity_guidance_uses_her_name_without_ai_self_labelling():
    from stella.ai_core import STELLA_IDENTITY_GUIDANCE

    normalized = STELLA_IDENTITY_GUIDANCE.casefold()
    assert "eres stella" in normalized
    assert "no introduzcas espontáneamente" in normalized
    assert "no afirmes ser una persona humana" in normalized
    assert "responde de forma sencilla" in normalized


@pytest.mark.parametrize("question", [
    "¿quién eres?",
    "¿cómo te llamas?",
    "¿eres una IA?",
    "¿eres IA?",
    "¿eres un bot?",
    "¿eres un chatbot?",
    "¿eres un modelo de lenguaje?",
    "¿qué eres?",
])
def test_direct_identity_questions_keep_stella_identity(question):
    answer = resolve_community_faq(question)

    assert answer
    assert "stella" in answer.casefold()
    assert "modelo de lenguaje" not in answer.casefold()
    assert "inteligencia artificial" not in answer.casefold()
    assert "integración comunitaria" not in answer.casefold()


def test_stella_does_not_claim_to_be_a_human_when_asked():
    answer = resolve_community_faq("¿eres humana?")

    assert answer
    assert "no soy una persona humana" in answer.casefold()
    assert "inteligencia artificial" not in answer.casefold()


def test_general_conversation_prompt_does_not_frame_stella_as_an_ai():
    prompt = build_ai_instructions(frozenset(), "conversation")

    assert "Stella también está aquí para conversar" in prompt
    assert "la IA también está para conversar" not in prompt
    assert "No introduzcas espontáneamente explicaciones sobre inteligencia artificial" in prompt


def test_stella_never_guesses_a_person_gender_or_pronouns():
    from stella.ai import STELLA_SYSTEM
    from stella.ai_core import STELLA_IDENTITY_GUIDANCE
    from stella.persona import GENDER_NEUTRAL_GUIDANCE

    for prompt in (STELLA_SYSTEM, STELLA_IDENTITY_GUIDANCE):
        assert "nunca deduzcas ni presupongas" in prompt.casefold()
        assert "nombre, apodo" in prompt.casefold()
        assert "fotografía o avatar" in prompt.casefold()
        assert "mantén el lenguaje neutral" in prompt.casefold()

    assert "sexo, género, identidad de género o pronombres" in GENDER_NEUTRAL_GUIDANCE
    assert "no son intercambiables" in GENDER_NEUTRAL_GUIDANCE
    assert "cómo prefiere que te refieras" in GENDER_NEUTRAL_GUIDANCE


def test_all_general_ai_instruction_modes_include_neutral_person_references():
    from stella.ai_core import STELLA_IDENTITY_GUIDANCE, build_ai_instructions

    for intent in ("conversation", "action", "research", "multi_action"):
        prompt = build_ai_instructions(frozenset(), intent)
        assert STELLA_IDENTITY_GUIDANCE in prompt
        assert "no los infieras por su nombre" in prompt.casefold()



def test_stella_character_guidance_is_shared_by_all_conversation_prompts():
    from stella.ai import STELLA_SYSTEM
    from stella.ai_core import STELLA_IDENTITY_GUIDANCE, build_ai_instructions
    from stella.persona import STELLA_CHARACTER_GUIDANCE

    assert STELLA_CHARACTER_GUIDANCE in STELLA_SYSTEM
    assert STELLA_CHARACTER_GUIDANCE in STELLA_IDENTITY_GUIDANCE
    assert "opiniones" in STELLA_CHARACTER_GUIDANCE
    assert "no inventes vivencias físicas" in STELLA_CHARACTER_GUIDANCE
    assert "No interrumpas conversaciones al azar" in STELLA_CHARACTER_GUIDANCE

    for intent in ("conversation", "action", "research", "multi_action"):
        assert STELLA_CHARACTER_GUIDANCE in build_ai_instructions(frozenset(), intent)
