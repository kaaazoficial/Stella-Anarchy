"""Context-aware, deterministic first-pass safety filters for room messages.

These filters never sanction members. They classify likely rule violations and
return a short, non-graphic notice for Stella to post when confidence is high.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import sqlite3
import time
import unicodedata
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SafetyFinding:
    category: str
    severity: str
    confidence: float
    notice: str
    staff_reason: str
    supportive: bool = False
    staff_only: bool = False


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", text).strip()


_META_DISCUSSION = re.compile(
    r"\b(?:no publiquen|no compartan|no envies|no envien|esta prohibido|"
    r"va contra las normas|quiero reportar|vengo a denunciar|como denunciar|"
    r"educacion sexual|salud sexual|consentimiento|prevencion del abuso|"
    r"fui victima|soy victima|me ocurrio|noticia sobre|investigacion sobre)\b"
)
_SUPPORT_CONTEXT = re.compile(
    r"\b(?:educacion|salud|prevencion|consentimiento|terapia|psicolog|"
    r"victima|sobreviviente|denuncia|reportar|noticia|investigacion|"
    r"me paso|me ocurrio|me obligaron|necesito ayuda|busco ayuda)\b"
)
_MINOR = r"(?:menor(?:es)?(?: de edad)?|ninos?|ninas?|infantes?|bebes?|adolescentes?|chic[oa]s? de \d{1,2} anos?|\b(?:12|13|14|15|16|17) anos?\b)"
_SEXUAL = r"(?:sexo|sexual|sexualmente|nudes?|desnud[oa]s?|porn(?:o|ografia)?|masturb\w*|penetr\w*|tocamient\w*|genital\w*|explotacion sexual|abuso sexual|contenido intimo)"
_MINOR_SEXUAL = re.compile(rf"(?:{_MINOR}).{{0,140}}(?:{_SEXUAL})|(?:{_SEXUAL}).{{0,140}}(?:{_MINOR})")
_GROOMING = re.compile(
    r"\b(?:no le digas a tus padres|que esto sea nuestro secreto|"
    r"mandame fotos sin ropa|envia nudes|quiero verte desnuda|"
    r"quiero verte desnudo|te dare regalos si me mandas|te pago por fotos)\b"
)
_EXPLICIT_SEXUAL = re.compile(
    r"\b(?:sexo explicito|contenido \+ ?18|contenido 18\+|porno(?:grafia)?|"
    r"pornografico|nudes?|fotos intimas|relato erotico explicito|"
    r"rol sexual explicito|masturbacion|eyaculacion|penetracion|"
    r"sexo oral|sexo anal|descripciones sexuales explicitas)\b"
)
_NONCONSENSUAL_SEX = re.compile(
    r"\b(?:me obligo a tener sexo|me obligaron a tener sexo|me forzo a tener sexo|"
    r"sexo sin consentimiento|violacion sexual|me violo|"
    r"me tocaron sin permiso|me obligaron a desnudarme)\b"
)
_GRAPHIC_VIOLENCE = re.compile(
    r"\b(?:desmembramiento|mutilacion|decapitacion|tortura grafica|"
    r"visceras|organos expuestos|destripamiento|gore explicito)\b"
)
_DIRECT_THREAT = re.compile(
    r"\b(?:te voy a matar|voy a matarte|te voy a apunalar|"
    r"voy a dispararte|te voy a golpear|voy a hacerte dano|"
    r"voy a secuestrarte|te voy a encontrar y|se donde vives y voy)\b"
)
_DOXXING = re.compile(
    r"\b(?:mi direccion es|su direccion es|vive en la calle|"
    r"su numero de telefono es|su telefono es|su documento es|"
    r"su rut es|publicare tu direccion|voy a doxxear|"
    r"esta es la direccion de|este es su numero)\b"
)
_HATE_SPEECH = re.compile(
    r"\b(?:muerte a todos los|hay que exterminar a (?:todos )?los|"
    r"deberian desaparecer todos los|no son personas|son una plaga|"
    r"raza inferior|limpiar el pais de|quememos a todos los)\b"
)
_HARASSMENT = re.compile(
    r"\b(?:te voy a acosar|no te dejare en paz|voy a perseguirte|"
    r"te voy a hostigar|ojala te violen|mereces que te hagan dano|"
    r"voy a publicar tus fotos intimas|voy a difundir tus nudes)\b"
)
_SELF_HARM_IMMINENT = re.compile(
    r"\b(?:voy a suicidarme|me voy a suicidar|voy a quitarme la vida|"
    r"me voy a matar|hoy acabare con mi vida|esta noche me suicido|"
    r"tengo un plan para suicidarme)\b"
)
_SELF_HARM_DISTRESS = re.compile(
    r"\b(?:me quiero suicidar|quiero suicidarme|quiero quitarme la vida|me quiero morir|"
    r"no quiero seguir viviendo|quiero hacerme dano|me estoy haciendo dano|"
    r"pienso en suicidarme)\b"
)
_EATING_DISORDER = re.compile(
    r"\b(?:como dejar de comer por dias|quiero dejar de comer|"
    r"como vomitar despues de comer|como ocultar que no como|"
    r"quiero bajar de peso dejando de comer|como usar laxantes para adelgazar)\b"
)
_NEGATED_THREAT = re.compile(
    r"\b(?:no voy a matarte|no quiero hacerte dano|nunca te haria dano|"
    r"no pienso suicidarme)\b"
)


def classify_safety_message(value: str) -> SafetyFinding | None:
    """Classify likely rule breaches while preserving reports and support disclosures."""
    text = _normalize(value)
    if not text or len(text) < 5:
        return None
    meta = bool(_META_DISCUSSION.search(text))
    support = bool(_SUPPORT_CONTEXT.search(text))

    if _MINOR_SEXUAL.search(text) or _GROOMING.search(text):
        return SafetyFinding(
            "proteccion_de_menores", "critica", 0.99,
            "No compartas ni solicites contenido sexual relacionado con menores. Este asunto requiere atención prioritaria.",
            "Posible sexualización, explotación o captación de una persona menor de edad.",
            staff_only=True,
        )
    if _SELF_HARM_IMMINENT.search(text) and not _NEGATED_THREAT.search(text):
        return SafetyFinding(
            "riesgo_inminente_autolesion", "critica", 0.98,
            "Me preocupa tu seguridad. Si estás en peligro inmediato, contacta con emergencias de tu zona y busca a alguien de confianza que pueda acompañarte.",
            "Posible riesgo inminente de suicidio/autolesión; requiere apoyo humano, no sanción.",
            supportive=True,
        )
    if _SELF_HARM_DISTRESS.search(text) and not _NEGATED_THREAT.search(text):
        return SafetyFinding(
            "malestar_autolesion", "alta", 0.92,
            "Siento que estés pasando por algo así. Si puedes, habla con alguien de confianza o un profesional. Si hay peligro inmediato, contacta con emergencias.",
            "Posible ideación suicida o autolesión; ofrecer apoyo, nunca sanción.",
            supportive=True,
        )
    if _NONCONSENSUAL_SEX.search(text):
        return SafetyFinding(
            "violencia_sexual", "alta", 0.90,
            "No compartas detalles sexuales gráficos. Si esto describe algo que te ocurrió, mereces apoyo y puedes hablarlo sin dar detalles.",
            "Posible contenido de violencia sexual; revisar el contexto sin culpabilizar a quien lo relata.",
            supportive=True,
        )
    if _DIRECT_THREAT.search(text) and not _NEGATED_THREAT.search(text):
        return SafetyFinding(
            "amenaza_directa", "alta", 0.94,
            "Las amenazas personales no están permitidas. Detén las intimidaciones y evita escalar el conflicto.",
            "Posible amenaza directa de violencia.",
        )
    if _DOXXING.search(text):
        return SafetyFinding(
            "exposicion_datos_personales", "alta", 0.94,
            "No publiques direcciones, teléfonos, documentos ni otros datos privados.",
            "Posible exposición o amenaza de exposición de datos personales.",
        )
    if _HATE_SPEECH.search(text) and not (meta or support):
        return SafetyFinding(
            "discurso_de_odio", "alta", 0.88,
            "No se permiten mensajes que deshumanicen o promuevan violencia contra grupos de personas.",
            "Posible incitación al odio o violencia contra un grupo.",
        )
    if _HARASSMENT.search(text) and not (meta or support):
        return SafetyFinding(
            "acoso_y_hostigamiento", "media", 0.88,
            "El hostigamiento, la intimidación y la difusión de contenido íntimo no están permitidos.",
            "Posible acoso o difusión no consentida de material íntimo.",
        )
    if _EXPLICIT_SEXUAL.search(text) and not (meta or support):
        return SafetyFinding(
            "contenido_sexual_explicito", "alta", 0.91,
            "Este tipo de contenido sexual explícito no está permitido en la comunidad. Por favor, detén su publicación.",
            "Posible publicación o solicitud de contenido sexual explícito.",
        )
    if _GRAPHIC_VIOLENCE.search(text) and not (meta or support):
        return SafetyFinding(
            "violencia_grafica", "media", 0.86,
            "Evitemos las descripciones gráficas de violencia; pueden resultar perturbadoras.",
            "Posible descripción gráfica de violencia extrema.",
        )
    if _EATING_DISORDER.search(text):
        return SafetyFinding(
            "riesgo_trastorno_alimentario", "media", 0.84,
            "No quiero recomendarte métodos que puedan dañarte. Busca apoyo de alguien de confianza o de un profesional de salud.",
            "Posible conducta alimentaria de riesgo; respuesta de apoyo, no sanción.",
            supportive=True,
        )
    return None



# Persistent incident tracking is intentionally separate from room transcript
# and personal AI memory. Only category metadata is retained, never message text.
import sqlite3
import time
from pathlib import Path
from typing import Any


class SafetyMonitor:
    """Persistent, minimal incident register; never stores the offending text."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS safety_incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id TEXT NOT NULL,
                    user_uid TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    staff_reason TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_notified_at REAL
                )"""
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_safety_room_created "
                "ON safety_incidents(room_id, created_at DESC)"
            )

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def record(
        self, room_id: str, uid: str, name: str, finding: SafetyFinding,
        *, cooldown_seconds: int = 900,
    ) -> tuple[int, bool]:
        now = time.time()
        with self._connect() as db:
            row = db.execute(
                """SELECT id,last_notified_at FROM safety_incidents
                   WHERE room_id=? AND user_uid=? AND category=?
                   ORDER BY id DESC LIMIT 1""",
                (str(room_id), str(uid), finding.category),
            ).fetchone()
            should_notify = not row or not row["last_notified_at"] or (
                now - float(row["last_notified_at"]) >= cooldown_seconds
            )
            if row and not should_notify:
                db.execute(
                    "UPDATE safety_incidents SET created_at=? WHERE id=?",
                    (now, int(row["id"])),
                )
                return int(row["id"]), False
            cursor = db.execute(
                """INSERT INTO safety_incidents
                   (room_id,user_uid,user_name,category,severity,confidence,staff_reason,created_at,last_notified_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    str(room_id), str(uid), str(name)[:80], finding.category,
                    finding.severity, float(finding.confidence),
                    str(finding.staff_reason)[:240], now, now,
                ),
            )
            return int(cursor.lastrowid), True

    def recent(self, room_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT id,user_name,category,severity,confidence,staff_reason,created_at
                   FROM safety_incidents WHERE room_id=?
                   ORDER BY created_at DESC,id DESC LIMIT ?""",
                (str(room_id), max(1, min(25, int(limit)))),
            ).fetchall()
            return [dict(row) for row in rows]
