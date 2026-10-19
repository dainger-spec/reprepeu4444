"""Отложенная репутация по нику.

Два источника: перенос из старого топа (файл rep_import.csv) и +rep @username
тем, кого бот ещё не видел. Бот не может узнать человека по нику (в Bot API нет
ни списка участников, ни поиска по нику), поэтому репутация начисляется, когда
человек с этим ником появляется в группе: пишет сообщение, вступает или на его
сообщение отвечают.
"""

import logging
from pathlib import Path

from aiogram import Bot

from .config import BASE_DIR
from .db import Database
from .tags import apply_tag, make_tag

log = logging.getLogger(__name__)

IMPORT_FILE = BASE_DIR / "rep_import.csv"


def parse_import_file(path: Path) -> list[tuple[str, int]]:
    """Строки «username,rep». Ник в нижнем регистре, без @; «*» в конце — неполный ник."""
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.lower().startswith("username"):
            continue
        username, rep = (x.strip() for x in line.split(",", 1))
        entries.append((username.lstrip("@").lower(), int(rep)))
    return entries


class RepImporter:
    def __init__(self) -> None:
        self._db: Database | None = None
        self._tag_format = ""
        # chat_id -> {ник: репутация} для ещё не начисленных записей переноса
        self._pending: dict[int, dict[str, int]] = {}
        # chat_id -> {ник: сколько +rep ждёт} для +rep @username
        self._deferred: dict[int, dict[str, int]] = {}

    async def load(self, db: Database, tag_format: str) -> None:
        self._db = db
        self._tag_format = tag_format
        for chat_id, username, rep in await db.pending_imports():
            self._pending.setdefault(chat_id, {})[username] = rep
        for chat_id, username, rep in await db.all_pending_rep():
            self._deferred.setdefault(chat_id, {})[username] = rep
        total = sum(len(x) for x in self._pending.values()) + sum(len(x) for x in self._deferred.values())
        if total:
            log.info("Ожидают начисления репутации по нику: %d", total)

    def _find(self, chat_id: int, username: str | None) -> str | None:
        entries = self._pending.get(chat_id)
        if not entries or not username:
            return None
        name = username.lower()
        if name in entries:
            return name
        for key in entries:
            if key.endswith("*") and name.startswith(key[:-1]):
                return key
        return None

    async def match(self, bot: Bot, chat_id: int, user_id: int, username: str | None) -> None:
        """Начисляет отложенную репутацию, если ник есть в списках. Профиль уже должен быть в БД."""
        db = self._db
        rep = 0
        # из словарей убираем сразу, до любых await, чтобы не начислить дважды
        key = self._find(chat_id, username)
        if key is not None:
            rep += self._pending[chat_id].pop(key)
            await db.mark_import_applied(chat_id, key, user_id)
        if username and self._deferred.get(chat_id):
            deferred = self._deferred[chat_id].pop(username.lower(), 0)
            if deferred:
                rep += deferred
                await db.delete_pending_rep(chat_id, username.lower())
        if not rep:
            return
        new_rep = await db.add_rep(chat_id, user_id, rep)
        log.info("Начислена отложенная репутация %+d → @%s (%s) в чате %s, теперь %d",
                 rep, username, user_id, chat_id, new_rep)
        member = await db.get(chat_id, user_id)
        if not member.banned:
            await apply_tag(bot, chat_id, user_id, make_tag(self._tag_format, new_rep))

    async def import_file(self, bot: Bot, chat_id: int, path: Path = IMPORT_FILE) -> tuple[int, int, int]:
        """Загружает файл для чата. Возвращает (новых записей, уже было, начислено сразу)."""
        added = skipped = 0
        for username, rep in parse_import_file(path):
            if await self._db.add_import(chat_id, username, rep):
                self._pending.setdefault(chat_id, {})[username] = rep
                added += 1
            else:
                skipped += 1

        # тем, кого бот уже знает в этом чате, начисляем сразу
        applied = 0
        for user_id, username in await self._db.users_with_username(chat_id):
            if self._find(chat_id, username) is not None:
                await self.match(bot, chat_id, user_id, username)
                applied += 1
        return added, skipped, applied

    async def defer(self, chat_id: int, username: str) -> int:
        """+1 к репутации человека, которого бот ещё не видел. Возвращает, сколько у него уже ждёт."""
        name = username.lstrip("@").lower()
        chat = self._deferred.setdefault(chat_id, {})
        chat[name] = chat.get(name, 0) + 1
        await self._db.add_pending_rep(chat_id, name, 1)
        return chat[name]

    def waiting(self, chat_id: int) -> list[str]:
        return sorted(self._pending.get(chat_id, {}))


importer = RepImporter()
