"""Command help matching the exact Anarchy [CB]/[C] presentation requested."""
from __future__ import annotations

import unicodedata
from functools import lru_cache


SEPARATOR = "[C] ───────────── ✦ ─────────────"
CATEGORY_TITLES = (
    "1. AYUDA E INTELIGENCIA ARTIFICIAL",
    "2. SOCIAL Y PERFILES",
    "3. JUEGOS, ECONOMÍA Y COMUNIDAD",
    "4. MÚSICA Y VOZ",
    "5. UTILIDADES Y MULTIMEDIA",
    "6. ADMINISTRACIÓN Y ESTADO",
)


def _command_help_sections(prefix: str):
    prefix = str(prefix or "!").strip() or "!"

    sections = [
        (CATEGORY_TITLES[0], [
            (True, "▸ Ayuda y Navegación"),
            (False, f"• {prefix}ayuda [categoría] — Muestra una sección concreta."),
            (False, f"• {prefix}menu — Muestra el catálogo completo por categorías."),
            (True, "▸ Asistente IA"),
            (False, f"• {prefix}asistente <consulta> — Conversación y solicitudes en lenguaje natural."),
            (False, f"• {prefix}ia ayuda — Guía de IA."),
            (False, f"• {prefix}ia preguntar <consulta> — Consulta directa al asistente."),
            (False, f"• {prefix}ia estado — Revisa el proveedor y la sesión actual."),
            (False, f"• {prefix}ia olvidar — Borra el contexto temporal de la charla."),
            (True, "▸ Investigación y Herramientas"),
            (False, f"• {prefix}investigar <tema> — Investigación (opciones: comparar, verificar, cronología, informe)."),
            (False, f"• {prefix}analizartexto <modo> <texto> — Modos: resumir, corregir, reescribir, extraer o contradicciones."),
            (False, f"• {prefix}ortografia <texto> — Sugiere correcciones solo cuando son claras."),
            (False, f"• {prefix}calcular <operación> — Porcentajes, raíces, trigonometría y matemáticas."),
            (True, "▸ Administración de IA [ADMIN]"),
        ]),
        (CATEGORY_TITLES[1], [
            (True, "▸ Perfil"),
            (False, f"• {prefix}perfil [@usuario] — Consulta un perfil."),
            (False, f"• {prefix}bio <texto> — Edita tu biografía."),
            (False, f"• {prefix}miestado <texto> — Actualiza tu estado."),
            (False, f"• {prefix}actividad [hoy|semana] — Consulta la actividad."),
            (True, "▸ Interacciones (@usuario)"),
            (False, f"• {prefix}interaccion @usuario — Guía de acciones sociales."),
            (False, f"• Afecto: {prefix}abrazo | {prefix}beso | {prefix}acariciar | {prefix}mimar"),
            (False, f"• Físicas: {prefix}patada | {prefix}golpe | {prefix}cachetada | {prefix}morder | {prefix}empujar"),
            (False, f"• Apoyo: {prefix}consolar | {prefix}felicitar | {prefix}saludar | {prefix}proteger | {prefix}ayudar | {prefix}apoyar"),
            (False, f"• Sociales: {prefix}bailar | {prefix}cosquillas | {prefix}chocar | {prefix}acompañar"),
            (False, f"• Otras: {prefix}asustar | {prefix}curar | {prefix}cargar | {prefix}regañar | {prefix}admirar | {prefix}dormir | {prefix}ignorar | {prefix}mirar | {prefix}invitar | {prefix}regalar"),
            (True, "▸ Vínculos y Regalos"),
            (False, f"• {prefix}roast @usuario — Lanza una broma."),
            (False, f"• {prefix}afinidad @usuario — Mide el vínculo con alguien."),
            (False, f"• {prefix}regalo @usuario [mensaje] — Envía un regalo."),
            (False, f"• {prefix}muro — Consulta los regalos recibidos."),
        ]),
        (CATEGORY_TITLES[2], [
            (True, "▸ Juegos y Entretenimiento"),
            (False, f"• {prefix}trivia — Inicia trivia | {prefix}responder <1-4> — Responde."),
            (False, f"• {prefix}adivina — Inicia adivinanza | {prefix}intento <número> — Prueba tu respuesta."),
            (False, f"• {prefix}duelo @usuario — Reta a alguien."),
            (False, f"• {prefix}ship <usuario1> <usuario2> — Calcula compatibilidad."),
            (False, f"• {prefix}azar <mín> <máx> | {prefix}azar dado | {prefix}azar moneda — Número aleatorio, dado o moneda."),
            (False, f"• {prefix}verdad | {prefix}8ball <pregunta> — Preguntas al azar."),
            (False, f"• {prefix}elige <opciones> — Elección aleatoria."),
            (False, f"• {prefix}chiste | {prefix}frase | {prefix}reto — Entretenimiento rápido."),
            (True, "▸ Economía"),
            (False, f"• {prefix}puntos — Consulta tu saldo."),
            (False, f"• {prefix}ranking — Clasificación general."),
            (False, f"• {prefix}diario — Reclama tu recompensa diaria."),
            (False, f"• {prefix}trabajar — Gana puntos."),
            (False, f"• {prefix}pagar @usuario <cantidad> — Transfiere puntos."),

            (True, "▸ Comunidad"),
            (False, f"• {prefix}encuesta <pregunta> | <opciones> — Crea una votación."),
            (False, f"• {prefix}evento | {prefix}eventos — Consulta, crea o cierra eventos (según permisos)."),
        ]),
        (CATEGORY_TITLES[3], [
            (True, "▸ Llamadas"),
            (False, f"• {prefix}join | {prefix}salirsala | {prefix}salas — Entrar, salir y consultar salas."),
            (False, f"• {prefix}voz participantes|hablando|estado — Consulta participantes y rol de hablante en tiempo real."),
            (True, "▸ Reproductor DJ"),
            (False, f"• {prefix}dj buscar <tema> | ambiente <mood> — Busca pistas con licencia abierta."),
            (False, f"• {prefix}dj reproducir <n> | encolar <n> — Reproducir o añadir resultados."),
            (False, f"• {prefix}dj pausa | {prefix}dj reanudar | {prefix}dj saltar | {prefix}dj cola | {prefix}dj actual | {prefix}dj resultados — Controles y resultados."),
            (False, f"• {prefix}dj detener | {prefix}dj volumen <0-100> | {prefix}dj fuentes — Controles y catálogos."),
            (False, f"• {prefix}dj quitar <n> | mover <a> <b> | vaciar | repetir off|una|cola — Gestiona la cola."),
            (False, f"• {prefix}dj favorito <n> | {prefix}dj favoritos — Guarda y consulta favoritos."),
            (False, f"• {prefix}dj probar — Diagnóstico de audio y LiveKit."),
            (False, f"• {prefix}reproducir <número> — Reproduce un resultado de búsqueda actual."),
            (True, "▸ Listas, Volumen y Lectura"),
            (False, f"• {prefix}playlist crear|lista|agregar|ver|reproducir|borrar — Guarda resultados de búsqueda en listas propias."),
            (False, f"• {prefix}volumen <0-100> | {prefix}subirvolumen | {prefix}bajarvolumen | {prefix}silencio — Controles de música."),
            (False, f"• {prefix}leer | {prefix}noleer — Activa o desactiva la lectura."),
            (False, f"• {prefix}tts voces|voz|velocidad|volumen|probar — Configura y prueba la lectura."),
            (False, f"• {prefix}volumenlectura <0-100> | {prefix}subirvolumenlectura | {prefix}bajarvolumenlectura | {prefix}silenciolectura — Controles de voz."),
        ]),
        (CATEGORY_TITLES[4], [
            (True, "▸ Fecha, Hora e Idiomas"),
            (False, f"• {prefix}fecha — Fecha y hora actual."),
            (False, f"• {prefix}zona <Area/Ciudad> — Hora de otra zona horaria."),
            (False, f"• {prefix}traducir <idioma> | <texto> — Traduce un texto."),
            (True, "▸ Búsqueda Multimedia"),
            (False, f"• {prefix}multimedia imagen|video|audio <búsqueda> — Búsqueda por tipo de recurso."),
            (False, f"• {prefix}imagen <búsqueda> — Busca imágenes."),
            (False, f"• {prefix}video <búsqueda> — Busca vídeos."),
            (False, f"• {prefix}audio <búsqueda> — Busca recursos de audio."),
        ]),
        (CATEGORY_TITLES[5], [
            (True, "▸ Identidad y Estado"),
            (False, f"• {prefix}miuid — Consulta tu UID."),
            (False, f"• {prefix}estado — Estado general del bot [ADMIN]."),
            (True, "▸ Fuentes y Paneles"),
            (False, f"• {prefix}fuentes — Estado de proveedores [ADMIN]."),
            (False, f"• {prefix}metricas — Métricas agregadas del sistema [ADMIN]."),
            (False, f"• {prefix}autotest — Ejecuta pruebas internas [ADMIN]."),
            (False, f"• {prefix}centro — Resumen de administración [ADMIN]."),
            (False, f"• {prefix}admin — Resumen de administración [ADMIN]."),
            (True, "▸ Moderación y Configuración"),
            (False, f"• {prefix}antispam activar|desactivar — Activa o desactiva el antispam por sala [ADMIN]."),
            (False, f"• {prefix}advertir @usuario [motivo] | {prefix}advertencias [@usuario] | {prefix}quitaradvertencia ID [ADMIN]."),
            (False, f"• {prefix}respuesta añadir|quitar|lista — Gestiona respuestas automáticas [ADMIN]."),
            (False, f"• {prefix}perfilbot Nombre | Estado — Edita la identidad del bot [ADMIN]."),
            (False, "Nota: Los comandos ADMIN requieren un UID autorizado en ADMIN_UIDS (el nickname no concede permisos)."),
        ]),
    ]
    return prefix, sections


def _format_section(title: str, entries: list[tuple[bool, str]]) -> list[str]:
    lines = [f"[CB] ✦ {title} ✦", "[C]"]
    for is_heading, value in entries:
        lines.append(f"[CB] {value}" if is_heading else f"[C] {value}")
    return lines


def _intro_lines(prefix: str) -> list[str]:
    accepted_prefixes = " / ".join(dict.fromkeys((prefix, "!", "-")))
    return [
        "[CB] ✦ STELLA · GUÍA DE COMANDOS ✦",
        f"[C] Prefijos aceptados: {accepted_prefixes}",
        f"[C] Usa {prefix}menu para consultar la guía completa por secciones.",
        "[C] Catálogo de comandos · Accesos y funciones.",
        "[C] Guía en texto, sin paneles ni botones.",
        "[C]",
    ]


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


@lru_cache(maxsize=8)
def build_commands_help_text(prefix: str = "!") -> str:
    """Build and cache the immutable full guide for each configured prefix."""
    prefix, sections = _command_help_sections(prefix)
    lines = _intro_lines(prefix)
    for index, (title, entries) in enumerate(sections):
        lines.extend([SEPARATOR, "[C]"])
        lines.extend(_format_section(title, entries))
    return "\n".join(lines)


@lru_cache(maxsize=16)
def _build_commands_help_blocks_cached(
    prefix: str = "!", max_units: int = 1800
) -> tuple[str, ...]:
    """Cache immutable blocks; callers receive a fresh list from the public API."""
    if max_units < 200:
        raise ValueError("El tamaño mínimo de bloque debe ser al menos 200 unidades UTF-16.")

    prefix, sections = _command_help_sections(prefix)
    intro = _intro_lines(prefix)
    blocks: list[str] = []
    for index, (title, entries) in enumerate(sections):
        category_lines = [
            SEPARATOR,
            "[C]",
            *_format_section(title, entries),
        ]
        if index == 0:
            category_lines = [*intro, *category_lines]
        block = "\n".join(category_lines)
        if _utf16_length(block) > max_units:
            raise ValueError(f"La categoría {title!r} supera el límite del mensaje.")
        blocks.append(block)
    return tuple(blocks)


def build_commands_help_blocks(prefix: str = "!", max_units: int = 1800) -> list[str]:
    """Return fresh category blocks backed by a short-lived immutable cache."""
    return list(_build_commands_help_blocks_cached(prefix, max_units))


_CATEGORY_ALIASES = {
    "ia": 0,
    "inteligencia": 0,
    "inteligencia artificial": 0,
    "social": 1,
    "perfiles": 1,
    "juegos": 2,
    "economia": 2,
    "comunidad": 2,
    "musica": 3,
    "voz": 3,
    "dj": 3,
    "tts": 3,
    "multimedia": 4,
    "utilidades": 4,
    "admin": 5,
    "administracion": 5,
}


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char)).strip()


def _help_prefix(help_text: str) -> str:
    for line in str(help_text).splitlines():
        if line.startswith('[C] Prefijos aceptados: '):
            return line.removeprefix('[C] Prefijos aceptados: ').split(' / ', 1)[0].strip() or '!'
        if line.startswith('[C] Prefijo principal: '):
            return line.removeprefix('[C] Prefijo principal: ').split(' (', 1)[0].strip() or '!'
    return '!'

@lru_cache(maxsize=64)
def filter_commands_help_text(help_text: str, topic: str | None = None) -> str:
    """Cache immutable full/category help text for frequently requested topics."""
    requested = _normalize(topic or "")
    if not requested or requested in {"todo", "todos", "completo", "all"}:
        return str(help_text)

    category_index = _CATEGORY_ALIASES.get(requested)
    if category_index is None:
        prefix = _help_prefix(help_text)
        return (
            "[CB] ✦ STELLA · GUÍA DE COMANDOS ✦\n"
            f"[C] Categoría no reconocida. Disponibles: ia, social, juegos, musica, multimedia y admin.\n"
            f"[C] Ejemplo: {prefix}ayuda social."
        )

    prefix = _help_prefix(help_text)

    _, sections = _command_help_sections(prefix)
    title, entries = sections[category_index]
    return "\n".join([
        *_intro_lines(prefix),
        SEPARATOR,
        "[C]",
        *_format_section(title, entries),
    ])
