"""Command catalogue and category filtering for Stella."""
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
        ("GENERAL E IA", [
            f"▸ {p}ayuda [categoría] · {p}menu · {p}ping · {p}miuid · {p}idbot",
            f"▸ {p}ia <consulta> · {p}asistente <consulta> · {p}ia estado|olvidar",
            f"▸ {p}investigar [comparar|verificar|cronologia|informe] <tema> · {p}analizar <texto o enlace>",
            f"▸ {p}analizartexto <resumir|corregir|reescribir|extraer|contradicciones> <texto>",
            f"▸ {p}traducir <idioma> | <texto> · {p}ortografia <texto> · {p}calcular <operación>",
            f"▸ {p}fecha · {p}zona <Area/Ciudad> · {p}azar <mín> <máx>",
        ]),
        ("SOCIAL Y PERFILES", [
            f"▸ {p}perfil [@usuario] · {p}bio <texto|-> · {p}miestado <texto|->",
            f"▸ {p}afinidad|amistad|reputacion @usuario · {p}regalo @usuario [mensaje] · {p}muro",
            f"▸ {p}puntos|saldo · {p}ranking|top · {p}diario|daily · {p}trabajar",
            f"▸ {p}pagar|transferir @usuario <cantidad> · {p}duelo @usuario · {p}ship <personas>",
            f"▸ {p}interaccion · {p}abrazo · {p}beso · {p}acariciar · {p}mimar · {p}consolar · {p}saludar",
            f"▸ {p}patada · {p}golpe · {p}cachetada · {p}morder · {p}empujar · {p}proteger · {p}ayudar · {p}apoyar",
            f"▸ {p}bailar · {p}cosquillas · {p}chocar · {p}acompañar · {p}asustar · {p}curar · {p}cargar · {p}regañar",
            f"▸ {p}admirar · {p}dormir · {p}ignorar · {p}mirar · {p}invitar · {p}regalar · {p}roast @usuario",
        ]),
        ("JUEGOS Y ENTRETENIMIENTO", [
            f"▸ {p}trivia · {p}responder <1-4> · {p}adivina · {p}intento <número>",
            f"▸ {p}verdad · {p}8ball <pregunta> · {p}elige A | B · {p}chiste · {p}frase · {p}reto",
            f"▸ {p}encuesta crear|votar|resultados|cerrar|lista · {p}evento crear|lista|apuntarse|cerrar",
        ]),
        ("MÚSICA, VOZ Y MULTIMEDIA", [
            f"▸ {p}join · {p}salirsala · {p}salas · {p}voz · {p}leer · {p}noleer · {p}tts voces|voz|velocidad|volumen",
            f"▸ {p}dj buscar|reproducir|encolar|cola|pausa|reanudar|saltar|detener · {p}playlist crear|lista|agregar|ver|reproducir|borrar",
            f"▸ {p}volumen <0-100> · {p}subirvolumen · {p}bajarvolumen · {p}silencio · {p}volumenlectura",
            f"▸ {p}multimedia imagen|video|audio <búsqueda> · {p}imagen · {p}video · {p}audio",
        ]),
        ("MODERACIÓN Y ADMINISTRACIÓN", [
            f"▸ {p}advertir <UID> | <motivo> · {p}advertencias [UID] · {p}quitaradvertencia <ID>",
            f"▸ {p}antispam activar|desactivar · {p}actividad · {p}respuesta añadir|quitar|lista",
            f"▸ {p}admin · {p}centro|nexus|paneladmin · {p}estado|sistema|diagnostico",
            f"▸ {p}fuentes|saludfuentes · {p}metricas|rendimiento · {p}autotest|laboratorio",
            f"▸ {p}perfilbot <nombre> | <estado> · {p}avatarbot <URL HTTPS>",
        ]),
    ]

def build_commands_help_text(prefix: str = "!") -> str:
    p = str(prefix or "!").strip() or "!"
    lines = ["[CB] ✦ STELLA · GUÍA DE COMANDOS ✦", f"[C] Prefijo principal: {p}", "[C] Usa !ayuda <categoría> para consultar una sección.", SEPARATOR]
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
    "ia": 0, "general": 0, "inteligencia": 0,
    "social": 1, "comunidad": 1, "perfiles": 1,
    "juegos": 2, "entretenimiento": 2,
    "musica": 3, "voz": 3, "dj": 3, "multimedia": 3,
    "admin": 4, "administracion": 4, "moderacion": 4,
}

@lru_cache(maxsize=64)
def filter_commands_help_text(help_text: str, topic: str | None = None) -> str:
    requested = _normalize(topic or "")
    if not requested or requested in {"todo", "todos", "completo", "all"}:
        return str(help_text)
    index = _CATEGORY_ALIASES.get(requested)
    if index is None:
        return "[CB] ✦ STELLA · GUÍA DE COMANDOS ✦\n[C] Categorías: ia, social, juegos, musica, admin.\n[C] Ejemplo: !ayuda social"
    prefix = "!"
    for line in str(help_text).splitlines():
        if line.startswith("[C] Prefijo principal: "):
            prefix = line.split(": ", 1)[1].strip() or "!"
            break
    title, entries = _sections(prefix)[index]
    return "\n".join(["[CB] ✦ STELLA · GUÍA DE COMANDOS ✦", SEPARATOR, f"[CB] {title}", *[f"[C] {entry}" for entry in entries]])
