"""Перенос репутации из старого топа (файл rep_import.csv).

У старых данных есть только ник, а не Telegram ID, поэтому репутация
начисляется, когда человек с этим ником появляется в группе: пишет сообщение,
вступает или на его сообщение отвечают.
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
        # chat_id -> {ник: репутация} для ещё не начисленных записей
        self._pending: dict[int, dict[str, int]] = {}

    async def load(self, db: Database, tag_format: str) -> None:
        self._db = db
        self._tag_format = tag_format
        for chat_id, username, rep in await db.pending_imports():
            self._pending.setdefault(chat_id, {})[username] = rep
        total = sum(len(x) for x in self._pending.values())
        if total:
            log.info("Ожидают переноса репутации: %d ник(ов)", total)

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
        """Начисляет перенесённую репутацию, если ник есть в списке. Профиль уже должен быть в БД."""
        key = self._find(chat_id, username)
        if key is None:
            return
        rep = self._pending[chat_id].pop(key)  # сразу убираем, чтобы не начислить дважды
        db = self._db
        new_rep = await db.add_rep(chat_id, user_id, rep)
        await db.mark_import_applied(chat_id, key, user_id)
        log.info("Перенесена репутация %+d → @%s (%s) в чате %s, теперь %d",
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

    def waiting(self, chat_id: int) -> list[str]:
        return sorted(self._pending.get(chat_id, {}))


importer = RepImporter()
