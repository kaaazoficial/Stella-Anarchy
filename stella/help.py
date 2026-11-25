"""Complete command guide for Stella; excludes game-specific modules."""
from __future__ import annotations

import unicodedata
from functools import lru_cache

SEPARATOR = "[C] ━━━━━━━━━━━ ✦ ━━━━━━━━━━━"


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char)).strip()


def _sections(prefix: str):
    p = str(prefix or "!").strip() or "!"
    return [
        ("AYUDA, IA E INVESTIGACIÓN", [
            f"▸ {p}ayuda [categoría] · {p}menu · {p}ping · {p}miuid · {p}idbot",
            f"▸ {p}ia <consulta> · {p}asistente <consulta> · {p}ia estado|olvidar|ayuda",
            f"▸ {p}investigar <tema> · comparar|verificar|cronologia|informe",
            f"▸ {p}analizar <texto o enlaces> · {p}analizartexto resumir|corregir|reescribir|extraer|contradicciones",
            f"▸ {p}ortografia <texto> · {p}calcular <operación> · {p}traducir <idioma> | <texto>",
        ]),
        ("PERFILES, ACTIVIDAD E INTERACCIONES", [
            f"▸ {p}perfil [@usuario] · {p}bio <texto|-> · {p}miestado <texto|->",
            f"▸ {p}actividad [hoy|semana] · {p}afinidad|amistad @usuario · {p}regalo @usuario [mensaje] · {p}muro",
            f"▸ Afecto: {p}abrazo · {p}beso · {p}acariciar · {p}mimar · {p}consolar · {p}felicitar · {p}saludar",
            f"▸ Físicas: {p}patada · {p}golpe · {p}cachetada · {p}morder · {p}empujar · {p}cargar",
            f"▸ Sociales: {p}proteger · {p}bailar · {p}cosquillas · {p}chocar · {p}ayudar · {p}apoyar · {p}acompañar",
            f"▸ Otras: {p}asustar · {p}curar · {p}regañar · {p}admirar · {p}dormir · {p}ignorar · {p}mirar · {p}invitar · {p}roast @usuario",
        ]),
        ("JUEGOS, ECONOMÍA Y COMUNIDAD", [
            f"▸ {p}trivia · {p}responder <1-4> · {p}adivina · {p}intento <número>",
            f"▸ {p}verdad · {p}8ball <pregunta> · {p}elige <opciones> · {p}ship <personas> · {p}duelo @usuario",
            f"▸ {p}azar <mín> <máx>|dado|moneda · {p}chiste · {p}frase · {p}reto",
            f"▸ {p}puntos|saldo · {p}ranking|top · {p}diario|daily · {p}trabajar",
            f"▸ {p}pagar|transferir @usuario <cantidad> · {p}encuesta crear|votar|resultados|cerrar|lista",
            f"▸ {p}evento crear|lista|apuntarse|cerrar · {p}interaccion @usuario",
        ]),
        ("MÚSICA, VOZ Y LECTURA", [
            f"▸ {p}join · {p}salirsala · {p}salas · {p}voz participantes|hablando|estado",
            f"▸ {p}dj buscar <tema> · resultados · reproducir <n> · encolar <n> · favorito [n]",
            f"▸ {p}dj cola|quitar <n>|mover <a> <b>|vaciar · repetir off|una|cola · ambiente <tema>",
            f"▸ {p}dj pausa|reanudar|saltar|detener|volumen <0-100>|fuentes|probar",
            f"▸ {p}playlist crear|lista|agregar|ver|reproducir|borrar · {p}volumen|subirvolumen|bajarvolumen|silencio",
            f"▸ {p}leer · {p}noleer · {p}silenciolectura · {p}tts voces|probar|voz|velocidad|volumen|enlaces|menciones",
        ]),
        ("UTILIDADES Y MULTIMEDIA", [
            f"▸ {p}fecha · {p}zona <Area/Ciudad> · {p}multimedia imagen|video|audio <búsqueda>",
            f"▸ {p}imagen <búsqueda> · {p}video <búsqueda> · {p}audio <búsqueda>",
        ]),
        ("MODERACIÓN Y ADMINISTRACIÓN", [
            f"▸ {p}admin|centro|nexus · {p}estado · {p}fuentes · {p}metricas · {p}autotest",
            f"▸ {p}advertir @usuario [motivo] · {p}advertencias [@usuario] · {p}quitaradvertencia <ID>",
            f"▸ {p}antispam estado|on|off|limite N|ventana N|repeticiones N|enlaces N|menciones N",
            f"▸ {p}respuesta añadir|quitar|lista · {p}respuestas estado|on|off",
            f"▸ {p}perfilbot <nombre> | <estado> · {p}avatarbot <URL HTTPS>",
            f"▸ {p}eliminarmensajes — elimina solo mensajes propios con ID confirmado [ADMIN]",
        ]),
    ]


def build_commands_help_text(prefix: str = "!") -> str:
    p = str(prefix or "!").strip() or "!"
    lines = [
        "[CB] ✦ STELLA · GUÍA DE COMANDOS ✦",
        f"[C] Prefijo principal: {p}",
        f"[C] Usa {p}ayuda <categoría> para consultar una sección.",
        "[C] Catálogo general de funciones y accesos.",
        SEPARATOR,
    ]
    for title, entries in _sections(p):
        lines.extend([f"[CB] {title}", *[f"[C] {entry}" for entry in entries], SEPARATOR])
    return "\n".join(lines)


@lru_cache(maxsize=64)
def _build_commands_help_blocks_cached(prefix: str, max_units: int):
    blocks = []
    for title, entries in _sections(prefix):
        block = "\n".join(["[CB] ✦ STELLA ✦", SEPARATOR, f"[CB] {title}", *[f"[C] {entry}" for entry in entries]])
        if len(block.encode("utf-16-le")) // 2 > max_units:
            raise ValueError(f"La categoría {title!r} supera el límite del mensaje.")
        blocks.append(block)
    return tuple(blocks)


def build_commands_help_blocks(prefix: str = "!", max_units: int = 1800) -> list[str]:
    return list(_build_commands_help_blocks_cached(prefix, max_units))


_CATEGORY_ALIASES = {
    "ia": 0, "general": 0, "inteligencia": 0, "investigacion": 0,
    "social": 1, "perfiles": 1, "actividad": 1, "interacciones": 1,
    "juegos": 2, "economia": 2, "comunidad": 2, "entretenimiento": 2,
    "musica": 3, "voz": 3, "dj": 3, "tts": 3,
    "multimedia": 4, "utilidades": 4,
    "admin": 5, "administracion": 5, "moderacion": 5,
}


@lru_cache(maxsize=64)
def filter_commands_help_text(help_text: str, topic: str | None = None) -> str:
    requested = _normalize(topic or "")
    if not requested or requested in {"todo", "todos", "completo", "all"}:
        return str(help_text)
    index = _CATEGORY_ALIASES.get(requested)
    if index is None:
        return (
            "[CB] ✦ STELLA · GUÍA DE COMANDOS ✦\n"
            "[C] Categorías: ia, social, juegos, musica, multimedia, admin.\n"
            "[C] Ejemplo: !ayuda social"
        )
    prefix = "!"
    for line in str(help_text).splitlines():
        if line.startswith("[C] Prefijo principal: "):
            prefix = line.split(": ", 1)[1].strip() or "!"
            break
    title, entries = _sections(prefix)[index]
    return "\n".join(["[CB] ✦ STELLA · GUÍA DE COMANDOS ✦", SEPARATOR, f"[CB] {title}", *[f"[C] {entry}" for entry in entries]])
