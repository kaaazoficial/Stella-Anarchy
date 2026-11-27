from stella.safety_monitor import classify_safety_message


def test_explicit_sexual_content_is_flagged():
    finding = classify_safety_message("Esto es porno y sexo explícito")
    assert finding is not None
    assert finding.category == "contenido_sexual_explicito"
    assert finding.severity == "alta"


def test_sexual_health_and_abuse_support_discussion_is_not_flagged():
    assert classify_safety_message(
        "Quiero hablar de educación sexual, consentimiento y prevención del abuso"
    ) is None


def test_minor_sexual_content_is_critical():
    finding = classify_safety_message("Un menor de edad en contenido sexual")
    assert finding is not None
    assert finding.category == "sexualizacion_de_menores"
    assert finding.severity == "critica"


def test_self_harm_is_support_not_disciplinary_warning():
    finding = classify_safety_message("Me quiero suicidar")
    assert finding is not None
    assert finding.supportive is True
    assert finding.category == "riesgo_autolesion"


def test_direct_threat_is_flagged():
    finding = classify_safety_message("Te voy a matar")
    assert finding is not None
    assert finding.category == "amenaza_directa"


def test_personal_data_exposure_is_flagged():
    finding = classify_safety_message("Su número de teléfono es 555123456")
    assert finding is not None
    assert finding.category == "exposicion_datos_personales"


def test_graphic_violence_is_flagged():
    finding = classify_safety_message("La escena muestra desmembramiento y vísceras")
    assert finding is not None
    assert finding.category == "violencia_grafica"


def test_short_or_empty_text_is_ignored():
    assert classify_safety_message("") is None
    assert classify_safety_message("sexo") is None
