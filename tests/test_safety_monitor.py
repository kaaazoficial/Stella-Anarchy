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
