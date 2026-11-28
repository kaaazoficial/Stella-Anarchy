from stella.help import build_admin_commands_help_text, build_commands_help_text


def test_admin_help_catalog_contains_registered_admin_commands():
    text = build_admin_commands_help_text("!")
    for command in (
        "!admin", "!estado", "!fuentes", "!metricas", "!autotest",
        "!advertir", "!advertencias", "!quitaradvertencia",
        "!antispam", "!respuestas", "!perfilbot", "!avatarbot",
        "!eliminarmensajes", "!silenciarchat", "!hablarchat",
    ):
        assert command in text


def test_public_help_does_not_expose_admin_catalog():
    text = build_commands_help_text("!")
    assert "!admin" not in text
    assert "!advertir" not in text
    assert "!silenciarchat" not in text
