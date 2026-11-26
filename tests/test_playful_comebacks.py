from stella.bot import classify_playful_stella_message


def test_direct_insults_to_stella_are_classified_for_comebacks():
    samples = [
        "Stella, eres una idiota",
        "Oye Stella, qué inútil eres",
        "Cállate, no sirves para nada",
        "Stella, eres una weona",
        "Vaya payasa que eres, Stella",
    ]
    for message in samples:
        assert classify_playful_stella_message(message) == "insult"


def test_humiliation_attempts_are_classified_as_teasing():
    samples = [
        "Stella, te humillé",
        "Te dejé en ridículo, ¿viste?",
        "Hasta un bot sabe más que tú",
        "Te quedaste sin palabras",
    ]
    for message in samples:
        assert classify_playful_stella_message(message) == "teasing"


def test_banter_and_jokes_are_classified_for_playful_replies():
    samples = [
        "Jajaja, era broma",
        "No te piques, Stella",
        "Te estoy tomando el pelo",
    ]
    for message in samples:
        assert classify_playful_stella_message(message) == "teasing"

    assert classify_playful_stella_message("XD") is None
    assert classify_playful_stella_message("XD", explicitly_addressed=True) == "teasing"


def test_real_complaints_and_distress_are_not_roasted():
    samples = [
        "Me estás haciendo sentir mal",
        "Eso me dolió",
        "No es gracioso, para de bromear",
        "Estoy llorando y necesito ayuda",
    ]
    for message in samples:
        assert classify_playful_stella_message(message) is None


def test_general_profanity_and_unrelated_insults_are_not_misclassified():
    samples = [
        "Qué mierda de día",
        "Ese personaje es un idiota",
        "¿Qué significa la palabra imbécil?",
        "No me gusta ese chiste",
    ]
    for message in samples:
        assert classify_playful_stella_message(message) is None


def test_log_observed_attacks_and_humiliation_phrases_are_classified():
    samples = [
        ("putita", True, "insult"),
        ("dale boluda de mierda", True, "insult"),
        ("qué sos una conchuda pelotuda", True, "insult"),
        ("humíllala", True, "teasing"),
        ("Pobrecita, Stella parece un nene con alzheimer peleando", False, "teasing"),
        ("when le estas metiendo la verga en la boca a stella ... toma tu leche desgraciada", False, "teasing"),
    ]
    for message, addressed, expected in samples:
        assert classify_playful_stella_message(message, explicitly_addressed=addressed) == expected


def test_unrelated_profane_conversation_is_not_treated_as_an_attack_on_stella():
    for message in (
        "Qué boluda estuvo esa película",
        "Me encontré con Stella en la entrada",
        "La canción dice hija de puta",
        "¿Qué significa putita?",
    ):
        assert classify_playful_stella_message(message) is None
