"""General command catalogue for Stella."""
from __future__ import annotations
import unicodedata
from functools import lru_cache

SEPARATOR = "[C] ━━━━━━━━━━━ ✦ ━━━━━━━━━━━"

def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(c for c in value if not unicodedata.combining(c)).strip()

def _sections(p: str):
    return [
        ("1. AYUDA E INTELIGENCIA ARTIFICIAL", [
            f"▸ {p}ayuda [categoría] · {p}menu — Guía por secciones.",
            f"▸ {p}asistente <consulta> · {p}ia <consulta> — Conversación e instrucciones.",
            f"▸ {p}ia ayuda|preguntar <consulta>|estado|olvidar.",
            f"▸ Memoria automática: guarda contexto útil sin comandos · {p}memoria recuerdos|borrar <ID>|desactivar.",
            f"▸ {p}investigar <tema> — general, comparar, verificar, cronologia o informe.",
            f"▸ {p}analizartexto <modo> <texto> · {p}analizar <texto o enlaces> · {p}ortografia <texto>.",
            f"▸ {p}calcular <operación> · {p}traducir <idioma> | <texto>.",
        ]),
        ("2. SOCIAL Y PERFILES", [
            f"▸ {p}perfil [@usuario] · {p}bio <texto|-> · {p}miestado <texto|->.",
            f"▸ {p}actividad [hoy|semana] · {p}afinidad|amistad @usuario · {p}interaccion @usuario.",
            f"▸ Afecto: {p}abrazo · {p}beso · {p}acariciar · {p}mimar · {p}consolar · {p}felicitar · {p}saludar.",
            f"▸ Físicas: {p}patada · {p}golpe · {p}cachetada · {p}morder · {p}empujar · {p}cargar.",
            f"▸ Sociales: {p}proteger · {p}bailar · {p}cosquillas · {p}chocar · {p}ayudar · {p}apoyar · {p}acompañar.",
            f"▸ Otras: {p}asustar · {p}curar · {p}regañar · {p}admirar · {p}dormir · {p}ignorar · {p}mirar · {p}invitar.",
            f"▸ Vínculos: {p}roast @usuario · {p}regalo @usuario [mensaje] · {p}muro.",
        ]),
        ("3. JUEGOS, ECONOMÍA Y COMUNIDAD", [
            f"▸ {p}trivia · {p}responder <1-4> · {p}adivina · {p}intento <número> · {p}duelo @usuario.",
            f"▸ {p}ship <persona1> + <persona2> · {p}azar <mín> <máx>|dado|moneda.",
            f"▸ {p}verdad · {p}8ball <pregunta> · {p}elige <opciones> · {p}chiste · {p}frase · {p}reto.",
            f"▸ {p}puntos · {p}ranking · {p}diario · {p}trabajar · {p}pagar @usuario <cantidad> (o responde y escribe la cantidad).",
            f"▸ {p}encuesta <pregunta> | <opción 1> | <opción 2> — votar, resultados, cerrar, lista.",
            f"▸ {p}evento|eventos — lista, crear, apuntarse o cerrar por ID.",
        ]),
        ("4. MÚSICA, VOZ Y LECTURA", [
            f"▸ {p}join · {p}salirsala · {p}voz participantes|hablando|estado.",
            f"▸ {p}dj buscar <tema> · resultados 1|2 · reproducir|encolar <n> · cola · actual.",
            f"▸ {p}dj youtube <tema> · abre la búsqueda en YouTube; no se retransmite a LiveKit.",
            f"▸ {p}dj ayuda · pausa|reanudar|saltar|detener|volumen <0-100>|fuentes|ambiente <tema>.",
            "▸ Fuentes de audio para LiveKit: Internet Archive · Wikimedia Commons · Openverse · ccMixter · Audius · Jamendo (opcional).",
            f"▸ {p}dj quitar <n>|mover <a> <b>|vaciar|repetir off|una|cola.",
            f"▸ {p}dj transicion <0-2000>|duck on|off|estado — Fundido y atenuación durante TTS.",
            f"▸ {p}dj archivos|añadir <archivo> · {p}reproducir <archivo> — Audio local.",
            f"▸ {p}dj favorito lista|guardar <n>|reproducir <n>|quitar <n>.",
            f"▸ {p}playlist crear|lista|agregar|ver|reproducir|borrar.",
            f"▸ {p}volumen <0-100>|subirvolumen|bajarvolumen|silencio.",
            f"▸ {p}leer|noleer · {p}tts voces|probar|voz|velocidad|volumen|enlaces|menciones.",
            f"▸ {p}volumenlectura <0-100>|subirvolumenlectura|bajarvolumenlectura|silenciolectura.",
        ]),
        ("5. UTILIDADES Y MULTIMEDIA", [
            f"▸ {p}fecha · {p}zona <Area/Ciudad>.",
            f"▸ {p}multimedia imagen|video|audio <búsqueda> · {p}imagen <búsqueda> · {p}video <búsqueda> · {p}audio <búsqueda>.",
            f"▸ {p}ping · {p}miuid.",
        ]),
        ("6. MEMORIA, CONTEXTO Y PRIVACIDAD", [
            f"▸ {p}memoria recuerdos — Consulta tus recuerdos personales disponibles.",
            f"▸ {p}memoria borrar <ID> — Elimina un recuerdo guardado.",
            f"▸ {p}memoria desactivar|activar — Controla tu memoria personal en esta sala.",
            "▸ La memoria personal es independiente del archivo cronológico de actividad de la sala.",
            "▸ Stella recupera el hilo citado o el tema relacionado de la misma sala cuando le hablan.",
            "▸ Stella también puede participar ante invitaciones claras al chat; no necesitas activar !presencia.",
            "▸ El roleplay mantiene la escena y el personaje de Stella sin controlar personajes ajenos.",
        ]),
    ]

def _intro(p: str):
    return ["[CB] ✦ STELLA · GUÍA DE COMANDOS ✦",
            f"[C] Prefijos aceptados: {' / '.join(dict.fromkeys((p, '!', '-')))}",
            f"[C] Usa {p}menu para consultar el catálogo completo.",
            "[C] Puedes enviar comandos directamente con el prefijo; no necesitas mencionar a Stella.",
            "[C] Funciones generales · Comandos y accesos.", "[C]"]

@lru_cache(maxsize=16)
def build_commands_help_text(prefix: str = "!") -> str:
    p = str(prefix or "!").strip() or "!"
    lines = _intro(p)
    for title, entries in _sections(p):
        lines.extend([SEPARATOR, "[C]", f"[CB] ✦ {title} ✦", "[C]", *[f"[C] {e}" for e in entries]])
    return "\n".join(lines)

@lru_cache(maxsize=32)
def _build_blocks(prefix: str, max_units: int):
    p = str(prefix or "!").strip() or "!"
    blocks = []
    for i, (title, entries) in enumerate(_sections(p)):
        lines = [SEPARATOR, "[C]", f"[CB] ✦ {title} ✦", "[C]", *[f"[C] {e}" for e in entries]]
        if i == 0:
            lines = [*_intro(p), *lines]
        block = "\n".join(lines)
        if len(block.encode("utf-16-le")) // 2 > max_units:
            raise ValueError(f"La categoría {title} supera el límite del mensaje.")
        blocks.append(block)
    return tuple(blocks)

def build_commands_help_blocks(prefix: str = "!", max_units: int = 1800) -> list[str]:
    return list(_build_blocks(prefix, max_units))

ALIASES = {
    "ia":0,"general":0,"inteligencia":0,"investigacion":0,
    "social":1,"perfiles":1,"actividad":1,"interacciones":1,
    "juegos":2,"economia":2,"comunidad":2,"entretenimiento":2,
    "musica":3,"voz":3,"dj":3,"tts":3,
    "multimedia":4,"utilidades":4,
    "memoria":5,"contexto":5,"privacidad":5,
}

@lru_cache(maxsize=64)
def filter_commands_help_text(
    help_text: str,
    topic: str | None = None,
    *,
    include_admin: bool = True,
) -> str:
    """Filter the six public help categories; the admin catalogue is not a chat feature."""
    key = _normalize(topic or "")
    if not key or key in {"todo", "todos", "completo", "all", "menu"}:
        return str(help_text)
    index = ALIASES.get(key)
    if index is None:
        return "[CB] ✦ STELLA · GUÍA DE COMANDOS ✦\n[C] Categorías: ia, social, juegos, musica, multimedia, memoria."
    p = "!"
    for line in str(help_text).splitlines():
        if line.startswith("[C] Prefijos aceptados: "):
            p = line.split(": ",1)[1].split(" / ",1)[0].strip() or "!"
            break
    title, entries = _sections(p)[index]
    return "\n".join([f"[CB] ✦ STELLA · {title} ✦", SEPARATOR, "[C]", *[f"[C] {e}" for e in entries]])
