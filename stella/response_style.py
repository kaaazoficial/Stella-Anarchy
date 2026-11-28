"""Request-aware response styles shared by every Stella conversational path."""
from __future__ import annotations

import re
import unicodedata


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


_PATTERNS = {
    "emotional": re.compile(
        r"\b(?:me siento|estoy triste|estoy preocupado|me preocupa|me da miedo|"
        r"tengo miedo|me duele|me paso algo|estoy pasando por|necesito desahogarme|"
        r"no puedo mas|estoy agobiado|estoy agotado emocionalmente|fallecio|"
        r"murio|perdi a|me siento solo|me siento sola)\b"
    ),
    "troubleshooting": re.compile(
        r"\b(?:error|bug|falla|fallo|no funciona|no responde|se rompe|"
        r"se cierra|crashea|arregla|arreglar|soluciona|solucionar|depura|"
        r"depurar|problema tecnico|configurar|configuracion|no inicia|"
        r"no arranca|no conecta|no carga)\b"
    ),
    "comparison": re.compile(
        r"\b(?:compara|comparar|comparacion|diferencia entre|"
        r"pros y contras|versus|vs|cual conviene|cual es mejor|"
        r"que es mejor|mejor opcion|alternativas)\b"
    ),
    "advice": re.compile(
        r"\b(?:que me recomiendas|que recomiendas|me recomiendas|"
        r"que deberia|deberia|que hago|como deberia|consejo|"
        r"vale la pena|que opinas|tu opinion|que harias)\b"
    ),
    "creative": re.compile(
        r"\b(?:inventa|inventar|crea|crear|escribe|redacta|"
        r"disena|disena|ideas para|dame ideas|personaje|biografia|"
        r"historia|relato|poema|escena|dialogo|nombre para|"
        r"concepto creativo|worldbuilding)\b"
    ),
    "explanation": re.compile(
        r"\b(?:explica|explicame|que es|quien fue|quien es|"
        r"como funciona|por que|porque|significa|define|"
        r"definicion|en que consiste|para que sirve|"
        r"cual es la causa)\b"
    ),
    "social": re.compile(
        r"^(?:hola|buenas|buenos dias|buenas tardes|buenas noches|"
        r"que tal|como estas|gracias|muchas gracias|jaja+|jeje+|"
        r"xd+|lmao|te quiero|buen dia)[.!?¡¿ ]*$"
    ),
}

_GUIDANCE = {
    "emotional": (
        "Primero responde a la situación concreta y al tono emocional que expresa. "
        "Reconoce lo que la persona está viviendo sin minimizarlo ni dramatizarlo; "
        "no diagnostiques ni conviertas automáticamente el desahogo en una lista de consejos. "
        "Escucha y ofrece un paso práctico solo cuando lo pidan o realmente ayude."
    ),
    "troubleshooting": (
        "Aborda el problema de forma práctica. Distingue lo confirmado de las hipótesis, "
        "prioriza la causa más probable y da pasos concretos en orden. No enumeres soluciones "
        "al azar: empieza por la comprobación que más información aporta. Pide solo el dato "
        "técnico imprescindible si no se puede avanzar sin él."
    ),
    "comparison": (
        "Compara usando criterios relevantes para la decisión, no una lista genérica de ventajas. "
        "Explica el principal compromiso entre opciones y termina con una recomendación clara "
        "cuando los datos permitan elegir; condiciona la recomendación a prioridades explícitas."
    ),
    "advice": (
        "Da una postura útil y razonada en vez de esconderte detrás de un «depende». "
        "Explica brevemente por qué, señala riesgos o límites reales y propone una acción "
        "concreta. Separa hechos, suposiciones y opinión personal."
    ),
    "creative": (
        "Entrega directamente la idea, texto o creación solicitada. Respeta las condiciones "
        "de estilo, formato, universo y extensión; aporta detalles específicos y evita clichés, "
        "nombres reciclados y explicaciones sobre el proceso creativo salvo que las pidan."
    ),
    "explanation": (
        "Responde primero la pregunta central. Después desarrolla el mecanismo o motivo con "
        "lenguaje claro y un ejemplo concreto cuando ayude. Ajusta la profundidad al tema y "
        "a lo que se preguntó; no uses una introducción vacía ni simplifiques hasta perder precisión."
    ),
    "social": (
        "Participa como una interlocutora de la conversación. Reacciona al contenido específico "
        "con naturalidad y una extensión breve; no fuerces consejos, explicaciones ni una pregunta "
        "para mantener el turno."
    ),
    "direct": (
        "Contesta la pregunta de forma explícita desde el inicio. Añade solo las precisiones "
        "necesarias para que la respuesta sea útil y no ocultes una conclusión detrás de rodeos."
    ),
    "conversation": (
        "Responde como parte de una charla real: identifica qué aporta o expresa la persona "
        "y contesta a eso. No transformes todo comentario en una consulta, consejo o interrogatorio."
    ),
    "playful": (
        "La respuesta es una réplica juguetona dirigida a Stella. Sé ingeniosa y específica "
        "respecto al mensaje, ajusta la intensidad y evita insultos o remates prefabricados. "
        "No reutilices literalmente respuestas anteriores."
    ),
    "research": (
        "Expón el hallazgo principal antes del contexto. Distingue datos respaldados, "
        "interpretaciones y vacíos; no conviertas una fuente parcial en certeza ni repitas "
        "la misma idea con distintas palabras."
    ),
}


def classify_response_style(value: str, *, intent: str = "conversation", roleplay: bool = False) -> str:
    """Select a lightweight response mode from the current request, not old history."""
    text = str(value or "").strip()
    if roleplay or re.search(r"\*[^*\n]{2,}\*|^\s*[—–-]\s*\S|\b(?:roleplay|rolear|hagamos rol)\b", text, re.I | re.M):
        return "roleplay"
    if str(intent or "").casefold() == "playful":
        return "playful"
    folded = _fold(text)
    for style in ("emotional", "troubleshooting", "comparison", "advice", "creative", "explanation"):
        if _PATTERNS[style].search(folded):
            return style
    if _PATTERNS["social"].fullmatch(folded):
        return "social"
    if "?" in text or "¿" in text:
        return "direct"
    return "conversation"


def build_response_mode_guidance(
    value: str, *, intent: str = "conversation", roleplay: bool = False
) -> str:
    """Return concise style guidance tailored to the current message."""
    style = classify_response_style(value, intent=intent, roleplay=roleplay)
    if style == "roleplay":
        return (
            "MODO DE RESPUESTA: ROLEPLAY. Mantén la escena y el formato narrativo establecidos; "
            "interpreta únicamente a Stella, no controles al otro personaje y haz avanzar la escena "
            "sin decidir la reacción ajena."
        )
    if not str(value or "").strip():
        return ""
    return f"MODO DE RESPUESTA: {style.upper()}. {_GUIDANCE[style]}"
