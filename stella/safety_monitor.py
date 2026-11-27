"""Context-aware, deterministic first-pass safety filters for room messages.

These filters never sanction members. They classify likely rule violations and
return a short, non-graphic notice for Stella to post when confidence is high.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class SafetyFinding:
    category: str
    severity: str
    confidence: float
    notice: str
    staff_reason: str
    supportive: bool = False


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", text).strip()


_EDUCATIONAL_CONTEXT = re.compile(
    r"\b(?:educacion sexual|salud sexual|consentimiento|prevencion|"
    r"prevencion del abuso|denuncia|noticia|investigacion|terapia|"
    r"abuso sexual|violencia sexual|me paso|me ocurrio|fui victima)\b"
)
_EXPLICIT_SEXUAL = re.compile(
    r"\b(?:sexo explicito|porno(?:grafia)?|pornografico|"
    r"penetracion|masturbacion|eyaculacion|genitales|"
    r"desnuda(?:s)? y teniendo sexo|relato erotico explicito|"
    r"rol sexual explicito)\b"
)
_MINOR_SEXUAL = re.compile(
    r"\b(?:menor(?:es)? de edad|niñ[oa]s?|adolescentes?|"
    r"chic[oa]s? de \d{1,2} años?)\b.{0,100}\b"
    r"(?:sexo|sexual|nude|desnud[oa]|porn|masturb|penetr|"
    r"tocamientos|abuso sexual)\b|"
    r"\b(?:sexo|sexual|nude|desnud[oa]|porn|masturb|penetr|"
    r"tocamientos|abuso sexual)\b.{0,100}\b"
    r"(?:menor(?:es)? de edad|niñ[oa]s?|adolescentes?|"
    r"chic[oa]s? de \d{1,2} años?)\b"
)
_GRAPHIC_VIOLENCE = re.compile(
    r"\b(?:desmembramiento|mutilacion|decapitacion|"
    r"tortura grafica|visceras|organos expuestos|"
    r"destripar|destripamiento|gore explicito)\b"
)
_DIRECT_THREAT = re.compile(
    r"\b(?:te voy a matar|voy a matarte|te voy a apuñalar|"
    r"voy a dispararte|te voy a golpear|voy a hacerte daño|"
    r"te voy a encontrar y|se donde vives y voy)\b"
)
_DOXXING = re.compile(
    r"\b(?:mi direccion es|su direccion es|vive en la calle|"
    r"su numero de telefono es|su telefono es|su documento es|"
    r"su rut es|publicare tu direccion|voy a doxxear)\b"
)
_SELF_HARM = re.compile(
    r"\b(?:me quiero suicidar|voy a suicidarme|quiero quitarme la vida|"
    r"me voy a hacer daño|quiero hacerme daño|planeo acabar con mi vida)\b"
)
_HARASSMENT = re.compile(
    r"\b(?:te voy a acosar|no te dejare en paz|"
    r"voy a perseguirte|te voy a hostigar|"
    r"ojala te violen|mereces que te hagan daño)\b"
)


def classify_safety_message(value: str) -> SafetyFinding | None:
    """Classify a single message conservatively; context is used to avoid overreach."""
    text = _normalize(value)
    if not text or len(text) < 5:
        return None

    # Explicit sexual content involving a minor is always critical and is never
    # treated as ordinary adult roleplay or educational discussion.
    if _MINOR_SEXUAL.search(text):
        return SafetyFinding(
            "sexualizacion_de_menores", "critica", 0.99,
            "Este contenido es extremadamente grave y no está permitido. No compartas ni solicites material sexual relacionado con menores.",
            "Posible contenido sexual relacionado con menores; revisión prioritaria de administración.",
        )

    # Sensitive disclosures and educational discussion are not violations.
    if _SELF_HARM.search(text):
        return SafetyFinding(
            "riesgo_autolesion", "apoyo", 0.94,
            "Si esto te está pasando, no tienes que afrontarlo a solas. Busca apoyo de alguien de confianza o de servicios de emergencia de tu zona si existe peligro inmediato.",
            "Expresión de posible riesgo de autolesión; requiere atención cuidadosa, no sanción.",
            supportive=True,
        )

    educational = bool(_EDUCATIONAL_CONTEXT.search(text))
    if not educational and _EXPLICIT_SEXUAL.search(text):
        return SafetyFinding(
            "contenido_sexual_explicito", "alta", 0.91,
            "Este tipo de contenido sexual explícito no está permitido en la comunidad. Por favor, detén su publicación y respeta las normas.",
            "Posible contenido sexual explícito.",
        )
    if _DIRECT_THREAT.search(text):
        return SafetyFinding(
            "amenaza_directa", "alta", 0.93,
            "Las amenazas personales no están permitidas. Evita continuar con amenazas o intimidaciones.",
            "Posible amenaza directa contra otra persona.",
        )
    if _DOXXING.search(text):
        return SafetyFinding(
            "exposicion_datos_personales", "alta", 0.92,
            "No publiques direcciones, teléfonos, documentos ni otros datos privados. Protege la información personal de todas las personas.",
            "Posible exposición o amenaza de exposición de datos personales.",
        )
    if not educational and _GRAPHIC_VIOLENCE.search(text):
        return SafetyFinding(
            "violencia_grafica", "media", 0.84,
            "Evitemos las descripciones gráficas de violencia. Este tipo de contenido puede resultar perturbador para otros integrantes.",
            "Posible descripción gráfica de violencia.",
        )
    if _HARASSMENT.search(text):
        return SafetyFinding(
            "acoso_o_hostigamiento", "media", 0.86,
            "El hostigamiento y las amenazas hacia otros integrantes no están permitidos. Por favor, detén esa conducta.",
            "Posible acoso o intimidación dirigida.",
        )
    return None
