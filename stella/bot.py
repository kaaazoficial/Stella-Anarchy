from __future__ import annotations

import hashlib
import logging
import random
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

from stella.ai import StellaAI
from stella.config import Settings
from stella.language import split_arguments
from stella.moderation import inspect_links, sanitize_reason
from stella.platform import AnarchyClient
from stella.services import StellaServices
from stella.music_voice import MusicVoice
from stella.storage import Store
from stella.extra_commands import calculate, datetime_text, CalculationError

log = logging.getLogger("stella.bot")

MAX_TRACKED_USERS = 5000
ANTISPAM_WINDOW_SECONDS = 8
ANTISPAM_WARNING_COOLDOWN = 60


class StellaBot:
    def __init__(self, settings: Settings, store: Store, platform: AnarchyClient) -> None:
        self.settings = settings
        self.store = store
        self.platform = platform
        self.services = StellaServices()
        self.ai = StellaAI()
        community_token = settings.community_bot_token or settings.bot_token
        self.voice_platform = (
            AnarchyClient(settings.api_base, community_token)
            if community_token and community_token != settings.bot_token
            else platform
        )
        self.voice = MusicVoice(self.voice_platform)
        self.voice.ai = self.ai
        # Only hashes and timestamps are held here. Message text is not retained.
        self._recent: dict[tuple[str, str], deque[tuple[float, str]]] = defaultdict(deque)
        self._warned_until: dict[tuple[str, str], float] = {}
        self._auto_reply_cooldown: dict[tuple[str, int], float] = {}\n        # Game sessions exist only in RAM and expire automatically.\n        self._game_sessions: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _author(message: dict[str, Any]) -> tuple[str, str]:
        author = message.get("author") or {}
        uid = str(author.get("uid") or "").strip()
        name = str(author.get("nickname") or author.get("displayName") or "Usuario")[:80]
        return uid, name

    def _is_admin(self, uid: str) -> bool:
        return bool(uid) and uid in self.settings.admin_uids

    async def _reply(self, room_id: str, text: str, message: dict[str, Any]) -> None:
        reply_id = message.get("messageId") or message.get("id")
        await self.platform.send_message(
            str(room_id), str(text)[:7000],
            reply_id=str(reply_id) if reply_id else None,
        )

    async def handle_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "").strip().casefold()
        room_id = str(event.get("threadId") or event.get("thread_id") or "").strip()
        if not room_id:
            return
        if event_type in {"room.end", "room.kicked", "thread.removed"}:
            self._forget_room(room_id)
            await self.voice.leave_room(room_id, request_leave=False)
            self.voice.search_results.pop(room_id, None)
            return
        if event_type not in {"message", "message.created", "message_create"}:
            return
        message = event.get("message") or {}
        if not isinstance(message, dict):
            return
        uid, name = self._author(message)
        content = str(message.get("content") or "").strip()
        if not uid or not content or self._is_bot_message(message):
            return

        self.store.record_activity(room_id, uid)
        blocked, reason = self._check_antispam(room_id, uid, content)
        if blocked:
            if reason:
                warning_id = self.store.add_warning(
                    room_id, uid, name, "stella:auto", "Stella", reason
                )
                await self._reply(
                    room_id,
                    f"⚠️ {name}, detecté actividad repetitiva. Se registró una advertencia #{warning_id}.",
                    message,
                )
            return

        prefix = self.settings.command_prefix
        if content.startswith(prefix):
            await self._command(room_id, uid, name, content[len(prefix):].strip(), message)
            return
        if not content.startswith(self.settings.command_prefix):
            await self.voice.enqueue_speech(room_id, f"{name}: {content}")
        await self._check_auto_replies(room_id, content, message)

    @staticmethod
    def _is_bot_message(message: dict[str, Any]) -> bool:
        author = message.get("author") or {}
        return bool(author.get("isBot") or author.get("bot"))

    def _forget_room(self, room_id: str) -> None:
        for mapping in (self._recent, self._warned_until, self._auto_reply_cooldown):
            for key in list(mapping):
                if key[0] == room_id:
                    mapping.pop(key, None)

    def _check_antispam(self, room_id: str, uid: str, content: str) -> tuple[bool, str | None]:
        if self.store.get_setting(room_id, "antispam_enabled", "1") != "1":
            return False, None
        now = time.monotonic()
        # Expire every user's recent hashes globally, not only on their next message.
        for tracked, queue in list(self._recent.items()):
            while queue and now - queue[0][0] > ANTISPAM_WINDOW_SECONDS:
                queue.popleft()
            if not queue:
                self._recent.pop(tracked, None)
                self._warned_until.pop(tracked, None)
        for tracked, expiry in list(self._warned_until.items()):
            if expiry <= now:
                self._warned_until.pop(tracked, None)
        for tracked, expiry in list(self._auto_reply_cooldown.items()):
            if expiry <= now:
                self._auto_reply_cooldown.pop(tracked, None)

        key = (room_id, uid)
        if key not in self._recent and len(self._recent) >= MAX_TRACKED_USERS:
            oldest = min(self._recent, key=lambda item: self._recent[item][0][0] if self._recent[item] else now)
            self._recent.pop(oldest, None)
            self._warned_until.pop(oldest, None)
        queue = self._recent[key]
        digest = hashlib.sha256(content.casefold().encode("utf-8")).hexdigest()
        while queue and now - queue[0][0] > ANTISPAM_WINDOW_SECONDS:
            queue.popleft()
        queue.append((now, digest))
        repeats = sum(1 for _, item in queue if item == digest)
        if len(queue) > 7 or repeats >= 4:
            if self._warned_until.get(key, 0) <= now:
                self._warned_until[key] = now + ANTISPAM_WARNING_COOLDOWN
                queue.clear()
                return True, "Antispam automático: demasiados mensajes o contenido repetido."
            return True, None
        # Remove empty and expired identities instead of retaining every user ever seen.
        for tracked in list(self._recent):
            if tracked != key and not self._recent[tracked]:
                self._recent.pop(tracked, None)
                self._warned_until.pop(tracked, None)
        for tracked, expiry in list(self._warned_until.items()):
            if expiry <= now and tracked not in self._recent:
                self._warned_until.pop(tracked, None)
        return False, None

    async def _check_auto_replies(self, room_id: str, content: str, message: dict[str, Any]) -> None:
        normalized = " ".join(content.casefold().split())
        now = time.monotonic()
        for rule in self.store.auto_reply_rules(room_id):
            trigger = " ".join(str(rule["trigger_text"]).casefold().split())
            if trigger and normalized == trigger:
                key = (room_id, int(rule["id"]))
                if len(self._auto_reply_cooldown) >= 3000:
                    self._auto_reply_cooldown = {
                        existing: expiry
                        for existing, expiry in self._auto_reply_cooldown.items()
                        if expiry > now
                    }
                if self._auto_reply_cooldown.get(key, 0) > now:
                    continue
                self._auto_reply_cooldown[key] = now + 15
                await self._reply(room_id, str(rule["response_text"]), message)
                return

    async def _command(
        self, room_id: str, uid: str, name: str, raw: str, message: dict[str, Any]
    ) -> None:
        if not raw:
            return
        parts = raw.split(maxsplit=1)
        command = parts[0].casefold()
        args = parts[1].strip() if len(parts) > 1 else ""
        routes = {
            "ayuda": self._help_command, "help": self._help_command, "comandos": self._help_command, "menu": self._help_command,
            "ping": self._ping, "miuid": self._uid, "uid": self._uid,
            "idbot": self._bot_id, "botid": self._bot_id, "identidadbot": self._bot_id,
            "azar": self._random, "frase": self._quote,
            "estado": self._status, "status": self._status,
            "advertir": self._warn, "warn": self._warn,
            "advertencias": self._warnings, "warnings": self._warnings,
            "quitaradvertencia": self._unwarn, "unwarn": self._unwarn,
            "antispam": self._antispam,
            "encuesta": self._poll, "poll": self._poll,
            "evento": self._event, "eventos": self._event, "event": self._event,
            "actividad": self._activity,
            "respuesta": self._auto_reply, "respuestas": self._auto_reply,
            "traducir": self._translate, "translate": self._translate,
            "ia": self._ai, "asistente": self._ai,
            "investigar": self._research, "investigate": self._research,
            "analizar": self._inspect,
            "analizartexto": self._analyze_text,
            "calcular": self._calculate,
            "fecha": self._date_time, "zona": self._date_time,
            "perfil": self._profile,
            "interaccion": self._interaction_help,
            "roast": self._roast, "ship": self._ship,
            "trivia": self._trivia, "responder": self._trivia_answer,
            "adivina": self._guess, "intento": self._guess_attempt,
            "verdad": self._truth, "8ball": self._eight_ball,
            "elige": self._choose, "chiste": self._joke, "reto": self._challenge,
            "join": self._voice_command, "salirsala": self._voice_command,
            "leer": self._voice_command, "noleer": self._voice_command,
            "voz": self._voice_command, "tts": self._voice_command,
            "dj": self._voice_command, "reproducir": self._voice_command,
        }
        social_actions = {
            "abrazo", "abrazar", "beso", "besar", "patada", "golpe", "cachetada",
            "acariciar", "mimar", "morder", "consolar", "felicitar", "saludar",
            "proteger", "bailar", "cosquillas", "chocar", "highfive", "ayudar",
            "regalar", "acompañar", "asustar", "curar", "cargar", "empujar",
            "regañar", "admirar", "apoyar", "dormir", "ignorar", "mirar", "invitar",
        }
        if command in social_actions:
            message["_stella_social_action"] = command
            await self._social_action(room_id, uid, name, args, message)
            return
        handler = routes.get(command)
        if handler is None:
            await self._reply(room_id, f"No reconozco ese comando. Usa {self.settings.command_prefix}ayuda.", message)
            return
        message["_stella_command"] = command
        await handler(room_id, uid, name, args, message)

    async def _calculate(self, room_id, uid, name, args, message):
        try:
            result = calculate(args)
        except CalculationError as exc:
            await self._reply(room_id, f"Uso: !calcular <operación>\\n{exc}", message)
            return
        await self._reply(room_id, f"🧮 {args[:160]} = {result}", message)

    async def _date_time(self, room_id, uid, name, args, message):
        zone = args.strip() if str(message.get("_stella_command") or "") == "zona" else "America/Santiago"
        try:
            result = datetime_text(zone)
        except ValueError as exc:
            await self._reply(room_id, str(exc), message)
            return
        await self._reply(room_id, result, message)

    async def _analyze_text(self, room_id, uid, name, args, message):
        mode, separator, content = args.partition(" ")
        mode = mode.casefold().strip()
        allowed = {"resumir", "corregir", "reescribir", "extraer", "contradicciones"}
        if not separator or mode not in allowed or not content.strip():
            await self._reply(room_id, "Uso: !analizartexto resumir|corregir|reescribir|extraer|contradicciones <texto>", message)
            return
        instructions = {
            "resumir": "Resume el texto conservando sus ideas esenciales y sin añadir datos.",
            "corregir": "Corrige ortografía, gramática y concordancia. Conserva el significado y el estilo.",
            "reescribir": "Reescribe el texto con mayor claridad y naturalidad, sin cambiar la información.",
            "extraer": "Extrae los datos, nombres, fechas y puntos importantes que aparezcan explícitamente.",
            "contradicciones": "Identifica contradicciones internas. Distingue contradicciones reales de datos que simplemente no se relacionan.",
        }[mode]
        answer = await self.ai.ask(content[:5500], system=instructions)
        await self._reply(room_id, answer, message)

    async def _profile(self, room_id, uid, name, args, message):
        author = message.get("author") or {}
        nickname = str(author.get("nickname") or author.get("displayName") or name)[:80]
        await self._reply(
            room_id,
            f"👤 PERFIL DE {nickname}\\nUID: {uid}\\nEsta vista muestra únicamente los datos disponibles en el evento actual.",
            message,
        )

    async def _interaction_help(self, room_id, uid, name, args, message):
        await self._reply(
            room_id,
            "INTERACCIONES SOCIALES\\n"
            "Afecto: !abrazo, !beso, !acariciar, !mimar\\n"
            "Físicas: !patada, !golpe, !cachetada, !morder, !empujar\\n"
            "Apoyo: !consolar, !felicitar, !saludar, !proteger, !ayudar, !apoyar\\n"
            "Sociales: !bailar, !cosquillas, !chocar, !acompañar\\n"
            "Otras: !asustar, !curar, !cargar, !regañar, !admirar, !dormir, !ignorar, !mirar, !invitar, !regalar\\n"
            "Uso: !abrazo @usuario",
            message,
        )

    async def _social_action(self, room_id, uid, name, args, message):
        target = args.strip()
        if not target:
            await self._reply(room_id, f"Uso: !{message.get('_stella_social_action')} @usuario", message)
            return
        action = str(message.get("_stella_social_action") or "").casefold()
        labels = {
            "abrazo": "le da un abrazo a", "abrazar": "le da un abrazo a",
            "beso": "le da un beso a", "besar": "le da un beso a",
            "patada": "le lanza una patada a", "golpe": "intenta golpear a",
            "cachetada": "le da una cachetada a", "acariciar": "acaricia a",
            "mimar": "mima a", "morder": "intenta morder a", "consolar": "consuela a",
            "felicitar": "felicita a", "saludar": "saluda a", "proteger": "se pone delante de",
            "bailar": "invita a bailar a", "cosquillas": "le hace cosquillas a",
            "chocar": "choca los cinco con", "highfive": "choca los cinco con",
            "ayudar": "ayuda a", "regalar": "le entrega un detalle a", "acompañar": "acompaña a",
            "asustar": "intenta asustar a", "curar": "atiende a", "cargar": "carga a",
            "empujar": "empuja a", "regañar": "regaña a", "admirar": "admira a",
            "apoyar": "muestra su apoyo a", "dormir": "se queda dormido junto a",
            "ignorar": "decide ignorar a", "mirar": "se queda mirando a", "invitar": "invita a",
        }
        await self._reply(room_id, f"✨ {name} {labels.get(action, 'interactúa con')} {target}.", message)

    async def _roast(self, room_id, uid, name, args, message):
        target = args.strip()
        if not target:
            await self._reply(room_id, "Uso: !roast @usuario", message)
            return
        lines = [
            "tienes tanta suerte que hasta el azar te deja en visto.",
            "tu sentido de orientación necesita un mapa para encontrar el botón de salir.",
            "eres capaz de perder una discusión contra el autocorrector.",
            "tu Wi-Fi tiene más estabilidad que tus planes.",
        ]
        await self._reply(room_id, f"🔥 {target}, {random.choice(lines)}", message)

    async def _ship(self, room_id, uid, name, args, message):
        names = [part.strip() for part in re.split(r"\\s*(?:\\+|,|\\by\\b)\\s*", args, flags=re.I) if part.strip()]
        if len(names) != 2:
            await self._reply(room_id, "Uso: !ship <persona 1> + <persona 2>", message)
            return
        import hashlib
        digest = hashlib.sha256(("|".join(sorted(names, key=str.casefold))).casefold().encode("utf-8")).digest()
        score = int.from_bytes(digest[:2], "big") % 101
        await self._reply(room_id, f"💞 Compatibilidad de {names[0]} + {names[1]}: {score}%. Solo es un resultado recreativo.", message)

    async def _choose(self, room_id, uid, name, args, message):
        options = [item.strip() for item in args.split("|") if item.strip()]
        if len(options) < 2:
            options = [item.strip() for item in args.split(",") if item.strip()]
        if len(options) < 2:
            await self._reply(room_id, "Uso: !elige opción 1 | opción 2 | opción 3", message)
            return
        await self._reply(room_id, f"🎯 Elijo: {random.choice(options[:20])}", message)

    async def _truth(self, room_id, uid, name, args, message):
        prompts = [
            "¿Qué pequeño detalle puede mejorar tu día?",
            "¿Qué habilidad te gustaría aprender y por qué?",
            "¿Qué canción nunca te cansas de escuchar?",
            "¿Qué decisión te enseñó algo importante?",
            "¿Qué lugar te gustaría conocer?",
        ]
        await self._reply(room_id, "💬 " + random.choice(prompts), message)

    async def _eight_ball(self, room_id, uid, name, args, message):
        if not args.strip():
            await self._reply(room_id, "Uso: !8ball <pregunta>", message)
            return
        answers = ["Sí, parece probable.", "No lo veo claro.", "Las señales apuntan a que sí.", "Mejor espera un poco.", "No cuentes con ello.", "Todo indica que puede funcionar."]
        await self._reply(room_id, "🎱 " + random.choice(answers), message)

    async def _joke(self, room_id, uid, name, args, message):
        jokes = [
            "—¿Qué hace una abeja en el gimnasio? —¡Zum-ba!",
            "—¿Cuál es el colmo de un electricista? —No encontrar su corriente de trabajo.",
            "—¿Qué le dijo un bit al otro? —Nos vemos en el bus.",
            "—¿Por qué el libro de matemáticas estaba triste? —Porque tenía demasiados problemas.",
        ]
        await self._reply(room_id, random.choice(jokes), message)

    async def _challenge(self, room_id, uid, name, args, message):
        challenges = [
            "Escribe una historia usando solo cinco palabras.",
            "Recomienda una canción que represente tu semana.",
            "Describe tu personaje favorito sin decir su nombre.",
            "Menciona tres cosas que te gustaría aprender.",
        ]
        await self._reply(room_id, "🎯 Reto: " + random.choice(challenges), message)

    async def _trivia(self, room_id, uid, name, args, message):
        now = time.monotonic()
        questions = [
            ("¿Cuántos lados tiene un hexágono?", ["5", "6", "7", "8"], 1),
            ("¿Qué planeta es conocido como el planeta rojo?", ["Venus", "Marte", "Júpiter", "Mercurio"], 1),
            ("¿Cuánto es 9 × 7?", ["56", "62", "63", "72"], 2),
            ("¿Cuál es el océano más grande?", ["Atlántico", "Índico", "Ártico", "Pacífico"], 3),
        ]
        question = random.choice(questions)
        self._game_sessions[room_id] = {"kind": "trivia", "expires": now + 900, "answer": question[2]}
        options = "\\n".join(f"{i+1}. {value}" for i, value in enumerate(question[1]))
        await self._reply(room_id, f"🧠 TRIVIA\\n{question[0]}\\n{options}\\nResponde con !responder <1-4>. Tienes 15 minutos.", message)

    async def _trivia_answer(self, room_id, uid, name, args, message):
        game = self._game_sessions.get(room_id)
        if not game or game.get("kind") != "trivia" or game.get("expires", 0) < time.monotonic():
            self._game_sessions.pop(room_id, None)
            await self._reply(room_id, "No hay una trivia activa. Inicia una con !trivia.", message)
            return
        try:
            answer = int(args.strip()) - 1
        except ValueError:
            await self._reply(room_id, "Responde con un número del 1 al 4.", message)
            return
        if answer == game["answer"]:
            await self._reply(room_id, f"✅ ¡Correcto, {name}!", message)
            self._game_sessions.pop(room_id, None)
        else:
            await self._reply(room_id, "❌ Esa no es la respuesta. Puedes intentarlo otra vez.", message)

    async def _guess(self, room_id, uid, name, args, message):
        self._game_sessions[room_id] = {"kind": "guess", "expires": time.monotonic() + 900, "answer": random.randint(1, 20), "attempts": 0}
        await self._reply(room_id, "🔢 He pensado un número del 1 al 20. Tienes seis intentos: !intento <número>.", message)

    async def _guess_attempt(self, room_id, uid, name, args, message):
        game = self._game_sessions.get(room_id)
        if not game or game.get("kind") != "guess" or game.get("expires", 0) < time.monotonic():
            self._game_sessions.pop(room_id, None)
            await self._reply(room_id, "No hay una adivinanza activa. Inicia una con !adivina.", message)
            return
        try:
            value = int(args.strip())
        except ValueError:
            await self._reply(room_id, "Indica un número entero del 1 al 20.", message)
            return
        if not 1 <= value <= 20:
            await self._reply(room_id, "El número debe estar entre 1 y 20.", message)
            return
        game["attempts"] += 1
        if value == game["answer"]:
            await self._reply(room_id, f"🎉 ¡Correcto, {name}! Encontraste el número en {game['attempts']} intentos.", message)
            self._game_sessions.pop(room_id, None)
        elif game["attempts"] >= 6:
            await self._reply(room_id, f"Se acabaron los intentos. El número era {game['answer']}.", message)
            self._game_sessions.pop(room_id, None)
        else:
            hint = "mayor" if value < game["answer"] else "menor"
            await self._reply(room_id, f"Prueba otra vez: el número es {hint}. Intentos: {game['attempts']}/6.", message)

    async def _help_command(self, room_id, uid, name, args, message):
        await self._reply(room_id, self._help(args), message)

    async def _ping(self, room_id, uid, name, args, message):
        await self._reply(room_id, "Pong. Stella está activa.", message)

    async def _uid(self, room_id, uid, name, args, message):
        await self._reply(room_id, f"Tu UID de Anarchy es: {uid}", message)

    async def _bot_id(self, room_id, uid, name, args, message):
        if not self._is_admin(uid):
            await self._reply(room_id, "Solo un administrador puede consultar la identidad técnica del bot.", message)
            return
        identity = await self.platform.get_bot_identity()
        if not identity:
            await self._reply(room_id, "No pude verificar la identidad de Stella con Anarchy. Revisa el token y los permisos de la API.", message)
            return
        bot_id = str(identity.get("botId") or identity.get("bot_id") or identity.get("id") or "").strip()
        account_uid = str(identity.get("uid") or "").strip()
        if not bot_id:
            await self._reply(room_id, "Anarchy autenticó el token, pero no devolvió botId. No usaré el UID de cuenta como sustituto.", message)
            return
        response = f"ID de bot de Stella: {bot_id}"
        if account_uid:
            response += f"\nUID de cuenta asociado: {account_uid}"
        thread_ids = identity.get("threadIds") or identity.get("thread_ids")
        if isinstance(thread_ids, list) and thread_ids:
            response += "\nSalas vinculadas: " + ", ".join(str(item) for item in thread_ids[:20])
        await self._reply(room_id, response, message)

    async def _status(self, room_id, uid, name, args, message):
        if not self._is_admin(uid):
            await self._reply(room_id, "No tienes permiso para consultar el estado administrativo.", message)
            return
        await self._reply(
            room_id,
            "Stella está operativa. Moderación: 90 días; actividad: 7 días; encuestas y eventos: 30 días tras su creación o fecha.",
            message,
        )

    async def _random(self, room_id, uid, name, args, message):
        mode = args.casefold().strip()
        if mode in {"dado", "dice"}:
            result, label = str(random.randint(1, 6)), "🎲"
        elif mode in {"moneda", "coin"}:
            result, label = random.choice(["cara", "cruz"]), "🪙"
        else:
            values = re.findall(r"-?\d+", mode)
            if len(values) != 2:
                await self._reply(room_id, f"Uso: {self.settings.command_prefix}azar <mín> <máx>, dado o moneda.", message)
                return
            low, high = map(int, values)
            if low > high or high - low > 1_000_000_000:
                await self._reply(room_id, "Indica un rango válido de hasta mil millones.", message)
                return
            result, label = str(random.randint(low, high)), "🎯"
        await self._reply(room_id, f"{label} Resultado: {result}", message)

    async def _quote(self, room_id, uid, name, args, message):
        phrases = [
            "No todo progreso hace ruido; a veces consiste en seguir intentándolo.",
            "La constancia convierte los pasos pequeños en distancia recorrida.",
            "Escuchar antes de responder también es una forma de respeto.",
            "Una comunidad se construye con la participación de quienes la cuidan.",
        ]
        await self._reply(room_id, random.choice(phrases), message)

    async def _warn(self, room_id, moderator_uid, moderator_name, args, message):
        if not self._is_admin(moderator_uid):
            await self._reply(room_id, "Solo el equipo administrativo puede registrar advertencias.", message)
            return
        target, separator, reason = args.partition("|")
        target_uid = target.strip().lstrip("@")
        if not target_uid or target_uid == moderator_uid:
            await self._reply(room_id, f"Uso: {self.settings.command_prefix}advertir <UID> | <motivo>", message)
            return
        warning_id = self.store.add_warning(
            room_id, target_uid, target_uid, moderator_uid, moderator_name,
            sanitize_reason(reason if separator else ""),
        )
        await self._reply(room_id, f"Advertencia #{warning_id} registrada para UID {target_uid}. Es un registro interno: no elimina ni silencia mensajes en Anarchy.", message)

    async def _warnings(self, room_id, requester_uid, name, args, message):
        if not self._is_admin(requester_uid):
            await self._reply(room_id, "Solo el equipo administrativo puede consultar advertencias.", message)
            return
        target_uid = args.strip().lstrip("@")
        records = self.store.warnings(room_id, target_uid or None)
        if not records:
            await self._reply(room_id, "No hay advertencias registradas para esa consulta.", message)
            return
        lines = ["⚖️ REGISTRO DE MODERACIÓN"]
        for row in records[:10]:
            state = "activa" if row["active"] else "retirada"
            lines.append(f"#{row['id']} · {row['target_name']} · {state} · {row['reason']}")
        await self._reply(room_id, "\n".join(lines), message)

    async def _unwarn(self, room_id, moderator_uid, name, args, message):
        if not self._is_admin(moderator_uid):
            await self._reply(room_id, "Solo el equipo administrativo puede retirar advertencias.", message)
            return
        try:
            warning_id = int(args.strip())
        except ValueError:
            await self._reply(room_id, f"Uso: {self.settings.command_prefix}quitaradvertencia <ID>", message)
            return
        if self.store.remove_warning(room_id, warning_id, moderator_uid):
            await self._reply(room_id, f"La advertencia #{warning_id} fue retirada.", message)
        else:
            await self._reply(room_id, "No encontré una advertencia activa con ese ID en esta sala.", message)

    async def _antispam(self, room_id, uid, name, args, message):
        if not self._is_admin(uid):
            await self._reply(room_id, "Solo el equipo administrativo puede configurar el antispam.", message)
            return
        action = args.casefold()
        if action in {"activar", "on", "encender"}:
            self.store.set_setting(room_id, "antispam_enabled", "1")
            response = "El antispam está activado en esta sala."
        elif action in {"desactivar", "off", "apagar"}:
            self.store.set_setting(room_id, "antispam_enabled", "0")
            response = "El antispam está desactivado en esta sala."
        else:
            response = f"Uso: {self.settings.command_prefix}antispam activar|desactivar"
        await self._reply(room_id, response, message)

    async def _poll(self, room_id, uid, name, args, message):
        parts = args.split(maxsplit=1)
        action = parts[0].casefold() if parts else ""
        rest = parts[1] if len(parts) > 1 else ""
        if action in {"crear", "create"}:
            values = split_arguments(rest)
            if len(values) < 3 or not values[0]:
                await self._reply(room_id, f"Uso: {self.settings.command_prefix}encuesta crear Pregunta | opción 1 | opción 2 [| ...]", message)
                return
            question, options = values[0], [v for v in values[1:9] if v]
            try:
                poll_id = self.store.create_poll(room_id, uid, question, options)
            except ValueError:
                await self._reply(room_id, "Una encuesta necesita entre 2 y 8 opciones.", message)
                return
            await self._reply(room_id, f"📊 Encuesta #{poll_id}: {question}\n" + "\n".join(f"{i+1}. {option}" for i, option in enumerate(options)) + f"\nVota con {self.settings.command_prefix}encuesta votar {poll_id} <número>.", message)
        elif action in {"votar", "vote"}:
            values = rest.split()
            try:
                poll_id, option = int(values[0]), int(values[1]) - 1
            except (ValueError, IndexError):
                await self._reply(room_id, f"Uso: {self.settings.command_prefix}encuesta votar <ID> <opción>", message)
                return
            result = self.store.vote_poll(room_id, poll_id, uid, option)
            labels = {"ok": "Tu voto quedó registrado. Puedes cambiarlo mientras la encuesta siga abierta.", "missing": "No encontré esa encuesta en esta sala.", "closed": "Esa encuesta ya está cerrada.", "invalid_option": "Esa opción no existe."}
            await self._reply(room_id, labels.get(result, "No pude registrar el voto."), message)
        elif action in {"resultado", "resultados", "results"}:
            try:
                poll_id = int(rest.strip())
            except ValueError:
                await self._reply(room_id, "Uso: !encuesta resultados <ID>", message)
                return
            poll = self.store.get_poll(room_id, poll_id)
            if not poll:
                await self._reply(room_id, "No encontré esa encuesta en esta sala.", message)
                return
            lines = [f"📊 {poll['question']} · {poll['total_votes']} votos"]
            for index, option in enumerate(poll["options"]):
                count = poll["results"].get(index, 0)
                lines.append(f"{index+1}. {option}: {count}")
            lines.append("Estado: cerrada" if poll["closed"] else "Estado: abierta")
            await self._reply(room_id, "\n".join(lines), message)
        elif action in {"cerrar", "close"}:
            try:
                poll_id = int(rest.strip())
            except ValueError:
                await self._reply(room_id, "Uso: !encuesta cerrar <ID>", message)
                return
            result = self.store.close_poll(room_id, poll_id, uid, self._is_admin(uid))
            labels = {"ok": "Encuesta cerrada.", "missing": "No encontré esa encuesta.", "closed": "La encuesta ya estaba cerrada.", "denied": "Solo quien creó la encuesta o un administrador puede cerrarla."}
            await self._reply(room_id, labels.get(result, "No pude cerrar la encuesta."), message)
        elif action in {"lista", "listar", "list"}:
            polls = self.store.list_polls(room_id)
            await self._reply(room_id, "\n".join([ "📊 ENCUESTAS" ] + [f"#{p['id']} · {p['question']} · {'cerrada' if p['closed'] else 'abierta'}" for p in polls]) if polls else "No hay encuestas en esta sala.", message)
        else:
            await self._reply(room_id, f"Uso: {self.settings.command_prefix}encuesta crear|votar|resultados|cerrar|lista ...", message)

    async def _event(self, room_id, uid, name, args, message):
        parts = args.split(maxsplit=1)
        action = parts[0].casefold() if parts else ""
        rest = parts[1] if len(parts) > 1 else ""
        if action in {"crear", "create"}:
            values = split_arguments(rest)
            if len(values) < 2:
                await self._reply(room_id, f"Uso: {self.settings.command_prefix}evento crear 2026-12-01T19:00:00-03:00 | Título | Lugar | Descripción", message)
                return
            try:
                starts = datetime.fromisoformat(values[0].replace("Z", "+00:00"))
                if starts.tzinfo is None:
                    raise ValueError("timezone required")
                title = values[1]
                location = values[2] if len(values) > 2 else ""
                description = values[3] if len(values) > 3 else ""
                event_timestamp = starts.timestamp()
                if event_timestamp <= time.time() or event_timestamp > time.time() + 180 * 86400:
                    raise ValueError("event date must be within the next 180 days")
                event_id = self.store.create_event(room_id, uid, title, event_timestamp, location, description)
            except ValueError:
                await self._reply(room_id, "La fecha debe ser ISO 8601 e incluir zona horaria, por ejemplo 2026-12-01T19:00:00-03:00.", message)
                return
            await self._reply(room_id, f"📅 Evento #{event_id} creado: {title}\nFecha: {starts.isoformat()}\nLugar: {location or 'Por definir'}\nApúntate con {self.settings.command_prefix}evento apuntarse {event_id}.", message)
        elif action in {"lista", "listar", "list"}:
            events = self.store.list_events(room_id)
            if not events:
                await self._reply(room_id, "No hay eventos próximos registrados en esta sala.", message)
                return
            lines = ["📅 PRÓXIMOS EVENTOS"]
            for event in events:
                date_label = datetime.fromtimestamp(float(event["starts_at"]), timezone.utc).astimezone().isoformat(timespec="minutes")
                lines.append(f"#{event['id']} · {event['title']} · {date_label} · {event['attendees']} asistentes · {event['location']}")
            await self._reply(room_id, "\n".join(lines), message)
        elif action in {"apuntarse", "asistir", "rsvp"}:
            try:
                event_id = int(rest.strip())
            except ValueError:
                await self._reply(room_id, "Uso: !evento apuntarse <ID>", message)
                return
            result = self.store.rsvp_event(room_id, event_id, uid, name)
            labels = {"ok": "Tu asistencia quedó registrada.", "missing": "No encontré ese evento en esta sala.", "closed": "Ese evento está cerrado."}
            await self._reply(room_id, labels.get(result, "No pude registrar tu asistencia."), message)
        elif action in {"cerrar", "close"}:
            try:
                event_id = int(rest.strip())
            except ValueError:
                await self._reply(room_id, "Uso: !evento cerrar <ID>", message)
                return
            result = self.store.close_event(room_id, event_id, uid, self._is_admin(uid))
            labels = {"ok": "Evento cerrado.", "missing": "No encontré ese evento.", "closed": "El evento ya estaba cerrado.", "denied": "Solo quien creó el evento o un administrador puede cerrarlo."}
            await self._reply(room_id, labels.get(result, "No pude cerrar el evento."), message)
        else:
            await self._reply(room_id, f"Uso: {self.settings.command_prefix}evento crear|lista|apuntarse|cerrar ...", message)

    async def _activity(self, room_id, uid, name, args, message):
        if not self._is_admin(uid):
            await self._reply(room_id, "Solo el equipo administrativo puede consultar la actividad.", message)
            return
        rows = self.store.activity_summary(room_id)
        if not rows:
            await self._reply(room_id, "Todavía no hay actividad agregada en los últimos siete días.", message)
            return
        lines = ["📈 ACTIVIDAD · ÚLTIMOS SIETE DÍAS"]
        lines.extend(f"{index}. UID {row['user_uid']}: {row['total_messages']} mensajes" for index, row in enumerate(rows, 1))
        await self._reply(room_id, "\n".join(lines), message)

    async def _auto_reply(self, room_id, uid, name, args, message):
        if not self._is_admin(uid):
            await self._reply(room_id, "Solo el equipo administrativo puede configurar respuestas automáticas.", message)
            return
        parts = args.split(maxsplit=1)
        action = parts[0].casefold() if parts else ""
        rest = parts[1] if len(parts) > 1 else ""
        if action in {"añadir", "agregar", "add"}:
            values = split_arguments(rest, 2)
            if len(values) < 2 or not values[0] or not values[1]:
                await self._reply(room_id, "Uso: !respuesta añadir palabra o frase | respuesta", message)
                return
            rule_id = self.store.add_auto_reply(room_id, values[0], values[1])
            await self._reply(room_id, f"Respuesta automática #{rule_id} creada. Se activa cuando el mensaje coincide exactamente con el disparador.", message)
        elif action in {"quitar", "eliminar", "remove"}:
            try:
                rule_id = int(rest.strip())
            except ValueError:
                await self._reply(room_id, "Uso: !respuesta quitar <ID>", message)
                return
            await self._reply(room_id, "Respuesta eliminada." if self.store.remove_auto_reply(room_id, rule_id) else "No encontré esa respuesta.", message)
        elif action in {"lista", "listar", "list"}:
            rules = self.store.auto_reply_rules(room_id)
            lines = ["🤖 RESPUESTAS AUTOMÁTICAS"] + [f"#{r['id']} · {r['trigger_text']} → {r['response_text']}" for r in rules]
            await self._reply(room_id, "\n".join(lines), message)
        else:
            await self._reply(room_id, "Uso: !respuesta añadir|quitar|lista ...", message)

    async def _translate(self, room_id, uid, name, args, message):
        language, separator, text = args.partition("|")
        if not separator:
            await self._reply(room_id, f"Uso: {self.settings.command_prefix}traducir <idioma> | <texto>", message)
            return
        await self._reply(room_id, await self.ai.translate(language, text), message)

    async def _ai(self, room_id, uid, name, args, message):
        if args.casefold() in {"estado", "status"}:
            await self._reply(room_id, self.ai.status(), message)
            return
        if not args:
            await self._reply(
                room_id,
                f"Uso: {self.settings.command_prefix}ia <consulta> o {self.settings.command_prefix}ia estado. "
                "La petición se procesa sin guardarse como historial.",
                message,
            )
            return
        answer = await self.ai.ask(args)
        await self._reply(room_id, answer, message)

    async def _voice_command(self, room_id, uid, name, args, message):
        command = str(message.get("_stella_command") or "").casefold().strip()
        handled, response = await self.voice.handle_command(room_id, command, args)
        if handled:
            await self._reply(room_id, response, message)

    async def close(self) -> None:
        await self.voice.close()
        await self.ai.close()
        await self.services.close()
        if self.voice_platform is not self.platform:
            await self.voice_platform.close()
        await self.platform.close()

    async def _research(self, room_id, uid, name, args, message):
        results = await self.services.research(args)
        if not results:
            await self._reply(room_id, "No pude obtener resultados de búsqueda. Inténtalo de nuevo más tarde.", message)
            return
        lines = [f"🔎 RESULTADOS DE INVESTIGACIÓN · {args[:180]}"]
        for index, item in enumerate(results[:5], 1):
            lines.append(f"{index}. {item['title']}\n{item['domain']}\n{item['url']}")
            if item["snippet"]:
                lines.append(item["snippet"])
        await self._reply(room_id, "\n".join(lines), message)

    async def _inspect(self, room_id, uid, name, args, message):
        findings = inspect_links(args)
        if not findings:
            await self._reply(room_id, "No encontré enlaces en el texto indicado. Uso: !analizar <texto o enlaces>", message)
            return
        lines = ["🔗 REVISIÓN PRELIMINAR DE ENLACES"]
        for item in findings:
            lines.append(f"{item['risk'].upper()} · {item.get('domain', item['url'])}: {item['reason']}")
        lines.append("Esto es una revisión superficial del formato y dominio, no una garantía de seguridad.")
        await self._reply(room_id, "\n".join(lines), message)

    def _help(self, topic: str = "") -> str:
        p = self.settings.command_prefix
        sections = {
            "GENERAL E IA": [
                f"{p}ayuda [categoría] · {p}menu · {p}ping · {p}miuid · {p}perfil",
                f"{p}asistente <consulta> · {p}ia <consulta> · {p}ia estado",
                f"{p}investigar <tema> · {p}analizar <enlace> · {p}analizartexto <modo> <texto>",
                f"{p}calcular <operación> · {p}traducir <idioma> | <texto> · {p}fecha · {p}zona <Area/Ciudad>",
            ],
            "SOCIAL Y COMUNIDAD": [
                f"{p}interaccion · {p}abrazo · {p}beso · {p}acariciar · {p}consolar · {p}felicitar · {p}saludar",
                f"{p}patada · {p}golpe · {p}cachetada · {p}morder · {p}empujar · {p}proteger · {p}ayudar · {p}apoyar",
                f"{p}bailar · {p}cosquillas · {p}chocar · {p}acompañar · {p}asustar · {p}curar · {p}cargar · {p}regañar",
                f"{p}admirar · {p}dormir · {p}ignorar · {p}mirar · {p}invitar · {p}regalar · {p}roast @usuario · {p}ship A + B",
                f"{p}encuesta crear|votar|resultados|cerrar|lista · {p}evento crear|lista|apuntarse|cerrar",
            ],
            "JUEGOS": [
                f"{p}trivia · {p}responder <1-4> · {p}adivina · {p}intento <número>",
                f"{p}azar <mín> <máx> · {p}azar dado|moneda · {p}verdad · {p}8ball <pregunta> · {p}elige A | B",
                f"{p}chiste · {p}frase · {p}reto",
            ],
            "MODERACIÓN Y ADMINISTRACIÓN": [
                f"{p}advertir <UID> | <motivo> · {p}advertencias [UID] · {p}quitaradvertencia <ID>",
                f"{p}antispam activar|desactivar · {p}actividad · {p}respuesta añadir|quitar|lista · {p}estado",
            ],
            "MÚSICA Y VOZ": [
                f"{p}join · {p}salirsala · {p}voz · {p}leer · {p}noleer · {p}tts voces|voz|velocidad|volumen",
                f"{p}dj buscar · reproducir · encolar · cola · pausa · reanudar · saltar · detener · volumen",
            ],
        }
        aliases = {
            "ia": "GENERAL E IA", "inteligencia": "GENERAL E IA", "general": "GENERAL E IA",
            "social": "SOCIAL Y COMUNIDAD", "comunidad": "SOCIAL Y COMUNIDAD",
            "juegos": "JUEGOS", "entretenimiento": "JUEGOS",
            "admin": "MODERACIÓN Y ADMINISTRACIÓN", "administracion": "MODERACIÓN Y ADMINISTRACIÓN",
            "moderacion": "MODERACIÓN Y ADMINISTRACIÓN",
            "musica": "MÚSICA Y VOZ", "voz": "MÚSICA Y VOZ", "dj": "MÚSICA Y VOZ",
        }
        requested = str(topic or "").strip().casefold()
        selected = aliases.get(requested)
        if requested and requested not in {"todo", "todos", "completo", "all"} and not selected:
            return "Categorías disponibles: ia, social, juegos, comunidad, musica y admin.\nEjemplo: !ayuda social"
        lines = ["✦ STELLA · COMANDOS", f"Prefijo: {p}", f"Consulta una categoría con {p}ayuda <categoría>."]
        for title, entries in sections.items():
            if selected and title != selected:
                continue
            lines.append(f"\n{title}")
            lines.extend(entries)
        return "\n".join(lines)
