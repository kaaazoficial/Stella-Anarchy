from __future__ import annotations

import logging
import random
import re
import time
from collections import defaultdict, deque
from typing import Any

from stella.config import Settings
from stella.platform import AnarchyClient
from stella.storage import Store

log = logging.getLogger("stella.bot")


class StellaBot:
    def __init__(self, settings: Settings, store: Store, platform: AnarchyClient) -> None:
        self.settings = settings
        self.store = store
        self.platform = platform
        # Transient anti-spam counters contain hashes, never message text, and are RAM-only.
        self._recent: dict[tuple[str, str], deque[tuple[float, str]]] = defaultdict(deque)
        self._warned_until: dict[tuple[str, str], float] = {}

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
        await self.platform.send_message(str(room_id), text, reply_id=str(reply_id) if reply_id else None)

    async def handle_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "").strip().casefold()
        room_id = str(event.get("threadId") or event.get("thread_id") or "").strip()
        if not room_id:
            return
        if event_type in {"room.end", "room.kicked"}:
            self._recent = defaultdict(deque, {
                key: value for key, value in self._recent.items() if key[0] != room_id
            })
            return
        if event_type not in {"message", "message.created", "message_create"}:
            return
        message = event.get("message") or {}
        if not isinstance(message, dict):
            return
        uid, name = self._author(message)
        content = str(message.get("content") or "").strip()
        if not uid or not content:
            return
        if self._is_bot_message(message):
            return

        blocked, reason = self._check_antispam(room_id, uid, content)
        if blocked:
            if reason:
                warning_id = self.store.add_warning(
                    room_id, uid, name, "stella:auto", "Stella", reason
                )
                await self._reply(
                    room_id,
                    f"⚠️ {name}, detecté actividad repetitiva. Se registró una advertencia #{warning_id}. Si crees que es un error, contacta al equipo de moderación.",
                    message,
                )
            return

        prefix = self.settings.command_prefix
        if not content.startswith(prefix):
            return
        await self._command(room_id, uid, name, content[len(prefix):].strip(), message)

    @staticmethod
    def _is_bot_message(message: dict[str, Any]) -> bool:
        author = message.get("author") or {}
        return bool(author.get("isBot") or author.get("bot"))

    def _check_antispam(self, room_id: str, uid: str, content: str) -> tuple[bool, str | None]:
        if self.store.get_setting(room_id, "antispam_enabled", "1") != "1":
            return False, None
        now = time.monotonic()
        key = (room_id, uid)
        queue = self._recent[key]
        digest = __import__("hashlib").sha256(content.casefold().encode("utf-8")).hexdigest()
        while queue and now - queue[0][0] > 8:
            queue.popleft()
        queue.append((now, digest))
        repeats = sum(1 for _, item in queue if item == digest)
        if len(queue) > 7 or repeats >= 4:
            if self._warned_until.get(key, 0) <= now:
                self._warned_until[key] = now + 60
                queue.clear()
                return True, "Antispam automático: demasiados mensajes o contenido repetido."
            return True, None
        return False, None

    async def _command(
        self, room_id: str, uid: str, name: str, raw: str, message: dict[str, Any]
    ) -> None:
        if not raw:
            return
        parts = raw.split(maxsplit=1)
        command = parts[0].casefold()
        args = parts[1].strip() if len(parts) > 1 else ""

        if command in {"ayuda", "help", "comandos"}:
            await self._reply(room_id, self._help(), message)
        elif command in {"ping"}:
            await self._reply(room_id, "Pong. Stella está activa.", message)
        elif command in {"miuid", "uid"}:
            await self._reply(room_id, f"Tu UID de Anarchy es: {uid}", message)
        elif command in {"azar"}:
            await self._random(room_id, args, message)
        elif command in {"estado", "status"}:
            if not self._is_admin(uid):
                await self._reply(room_id, "No tienes permiso para consultar el estado administrativo.", message)
                return
            await self._reply(room_id, "Stella está operativa. Los datos de moderación se conservan durante un máximo de 90 días.", message)
        elif command in {"advertir", "warn"}:
            await self._warn(room_id, uid, name, args, message)
        elif command in {"advertencias", "warnings"}:
            await self._warnings(room_id, uid, args, message)
        elif command in {"quitaradvertencia", "unwarn"}:
            await self._unwarn(room_id, uid, args, message)
        elif command == "antispam":
            await self._antispam(room_id, uid, args, message)
        else:
            await self._reply(room_id, f"No reconozco ese comando. Usa {self.settings.command_prefix}ayuda.", message)

    def _help(self) -> str:
        p = self.settings.command_prefix
        return (
            "✦ STELLA · COMANDOS\n"
            f"{p}ayuda — muestra esta ayuda\n"
            f"{p}ping — comprueba que estoy activa\n"
            f"{p}miuid — muestra tu UID de Anarchy\n"
            f"{p}azar <mín> <máx> — número aleatorio\n"
            f"{p}azar dado — lanza un dado\n"
            f"{p}azar moneda — lanza una moneda\n"
            f"{p}advertir <UID> | <motivo> — registra una advertencia (moderación)\n"
            f"{p}advertencias [UID] — consulta advertencias (moderación)\n"
            f"{p}quitaradvertencia <ID> — retira una advertencia activa (moderación)\n"
            f"{p}antispam activar|desactivar — configura el filtro (administración)"
        )

    async def _random(self, room_id: str, args: str, message: dict[str, Any]) -> None:
        mode = args.casefold().strip()
        if mode in {"dado", "dice"}:
            result = str(random.randint(1, 6))
            label = "🎲"
        elif mode in {"moneda", "coin"}:
            result = random.choice(["cara", "cruz"])
            label = "🪙"
        else:
            values = re.findall(r"-?\d+", mode)
            if len(values) != 2:
                await self._reply(room_id, f"Uso: {self.settings.command_prefix}azar <mín> <máx>, o {self.settings.command_prefix}azar dado/moneda.", message)
                return
            low, high = map(int, values)
            if low > high or high - low > 1_000_000_000:
                await self._reply(room_id, "Indica un rango válido de hasta mil millones.", message)
                return
            result = str(random.randint(low, high))
            label = "🎯"
        await self._reply(room_id, f"{label} Resultado: {result}", message)

    async def _warn(self, room_id: str, moderator_uid: str, moderator_name: str, args: str, message: dict[str, Any]) -> None:
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
            reason.strip() if separator and reason.strip() else "Sin motivo indicado",
        )
        await self._reply(room_id, f"Advertencia #{warning_id} registrada para UID {target_uid}. Esto queda como registro interno; no elimina ni silencia mensajes en Anarchy.", message)

    async def _warnings(self, room_id: str, requester_uid: str, args: str, message: dict[str, Any]) -> None:
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
            status = "activa" if row["active"] else "retirada"
            lines.append(f"#{row['id']} · {row['target_name']} · {status} · {row['reason']}")
        await self._reply(room_id, "\n".join(lines), message)

    async def _unwarn(self, room_id: str, moderator_uid: str, args: str, message: dict[str, Any]) -> None:
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

    async def _antispam(self, room_id: str, uid: str, args: str, message: dict[str, Any]) -> None:
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
