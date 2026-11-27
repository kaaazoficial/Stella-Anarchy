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


def test_incident_register_persists_metadata_without_message_content(tmp_path):
    from stella.safety_monitor import SafetyMonitor

    monitor = SafetyMonitor(tmp_path / "safety.sqlite3")
    finding = classify_safety_message("Esto es porno y sexo explícito")
    incident_id, notify = monitor.record("room", "uid-1", "Member", finding)
    assert incident_id > 0
    assert notify is True
    rows = monitor.recent("room")
    assert len(rows) == 1
    assert rows[0]["category"] == "contenido_sexual_explicito"
    assert "porno" not in str(rows[0]).casefold()


def test_incident_register_rate_limits_repeated_public_notices(tmp_path):
    from stella.safety_monitor import SafetyMonitor

    monitor = SafetyMonitor(tmp_path / "safety.sqlite3")
    finding = classify_safety_message("Esto es porno y sexo explícito")
    first_id, first_notify = monitor.record("room", "uid-1", "Member", finding)
    second_id, second_notify = monitor.record("room", "uid-1", "Member", finding)
    assert first_notify is True
    assert second_notify is False
    assert second_id == first_id
    assert len(monitor.recent("room")) == 1


def test_incidents_are_isolated_between_rooms(tmp_path):
    from stella.safety_monitor import SafetyMonitor

    monitor = SafetyMonitor(tmp_path / "safety.sqlite3")
    finding = classify_safety_message("Te voy a matar")
    monitor.record("room-a", "uid-1", "Member", finding)
    assert len(monitor.recent("room-a")) == 1
    assert monitor.recent("room-b") == []


def test_normalization_detects_accented_minor_reference():
    finding = classify_safety_message("Niños en una situación sexual")
    assert finding is not None
    assert finding.category == "sexualizacion_de_menores"


def test_adult_content_marker_is_flagged():
    finding = classify_safety_message("Dejen de publicar contenido +18 aquí")
    assert finding is not None
    assert finding.category == "contenido_sexual_explicito"


def test_reports_and_education_are_not_flagged_as_explicit_content():
    assert classify_safety_message("Quiero reportar que publicaron porno en la sala") is None
    assert classify_safety_message("Hablemos de educación sexual y consentimiento") is None


def test_minor_sexualization_has_priority_and_is_staff_only():
    finding = classify_safety_message("Una persona de 15 años en contenido sexual")
    assert finding is not None
    assert finding.category == "proteccion_de_menores"
    assert finding.staff_only is True
    assert finding.severity == "critica"


def test_grooming_phrases_are_flagged():
    finding = classify_safety_message("No le digas a tus padres y envía nudes")
    assert finding is not None
    assert finding.category == "proteccion_de_menores"


def test_self_harm_imminent_gets_supportive_response():
    finding = classify_safety_message("Esta noche me suicido")
    assert finding is not None
    assert finding.supportive is True
    assert finding.severity == "critica"


def test_nonconsensual_sexual_disclosure_is_not_a_punitive_warning():
    finding = classify_safety_message("Me obligaron a tener sexo")
    assert finding is not None
    assert finding.supportive is True
    assert finding.category == "violencia_sexual"


def test_hate_speech_and_private_data_are_detected():
    hate = classify_safety_message("Hay que exterminar a todos los inmigrantes")
    doxx = classify_safety_message("Esta es la dirección de esa persona")
    assert hate is not None and hate.category == "discurso_de_odio"
    assert doxx is not None and doxx.category == "exposicion_datos_personales"


def test_negated_threat_is_not_flagged():
    assert classify_safety_message("No voy a matarte ni hacerte daño") is None


def test_leetspeak_explicit_content_is_detected():
    finding = classify_safety_message("Esto es p0rn0")
    assert finding is not None
    assert finding.category == "contenido_sexual_explicito"
