"""Какие группы может обслуживать бот.

Когда бота добавляют в группу, администратору бота в личку приходит запрос
с кнопками «Разрешить» / «Отклонить». Решения хранятся в БД. Группы из
ALLOWED_CHAT_ID разрешены сразу (если администратор не отключил их кнопкой).
"""

import logging
import time
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Chat, InlineKeyboardButton, InlineKeyboardMarkup, User

from .db import Database

log = logging.getLogger(__name__)

ALLOWED, DENIED, PENDING = "allowed", "denied", "pending"


class ChatAccess:
    def __init__(self) -> None:
        self._db: Database | None = None
        self._env: frozenset[int] = frozenset()
        self._status: dict[int, str] = {}
        self._titles: dict[int, str] = {}
        self._requested_at: dict[int, float] = {}

    async def load(self, db: Database, env_ids: frozenset[int]) -> None:
        self._db = db
        self._env = env_ids
        for chat_id, title, status in await db.all_chats():
            self._status[chat_id] = status
            self._titles[chat_id] = title

    def status(self, chat_id: int) -> str | None:
        if chat_id in self._status:
            return self._status[chat_id]
        return ALLOWED if chat_id in self._env else None

    def is_allowed(self, chat_id: int) -> bool:
        return self.status(chat_id) == ALLOWED

    def allowed_ids(self) -> list[int]:
        ids = {c for c, s in self._status.items() if s == ALLOWED}
        ids |= {c for c in self._env if c not in self._status}
        return sorted(ids)

    def title(self, chat_id: int) -> str:
        return self._titles.get(chat_id) or str(chat_id)

    def chats(self) -> list[tuple[int, str, str]]:
        """Все известные группы: (id, название, статус)."""
        ids = list(self._status) + [c for c in self._env if c not in self._status]
        return [(c, self.title(c), self.status(c)) for c in ids]

    def remember_title(self, chat_id: int, title: str | None) -> None:
        if title:
            self._titles[chat_id] = title

    async def set(self, chat_id: int, status: str, title: str | None = None) -> None:
        if title:
            self._titles[chat_id] = title
        self._status[chat_id] = status
        await self._db.set_chat_status(chat_id, self.title(chat_id), status)

    async def migrate(self, old_id: int, new_id: int, title: str | None) -> None:
        """Обычная группа стала супергруппой: новый ID получает тот же доступ и данные."""
        if self.status(new_id) == self.status(old_id) or not self.is_allowed(old_id):
            return
        await self._db.migrate_chat(old_id, new_id)
        await self.set(new_id, ALLOWED, title or self.title(old_id))
        log.info("Группа %s стала супергруппой %s — доступ и данные перенесены", old_id, new_id)


access = ChatAccess()


def decision_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Разрешить", callback_data=f"chat:allow:{chat_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"chat:deny:{chat_id}"),
    ]])


def chats_list(chat_access: ChatAccess) -> tuple[str, InlineKeyboardMarkup | None]:
    """Текст и кнопки для /chats."""
    chats = chat_access.chats()
    if not chats:
        return ("Бот пока не добавлен ни в одну группу.\n"
                "Добавьте его в группу — сюда придёт запрос на доступ."), None
    icons = {ALLOWED: "✅", DENIED: "❌", PENDING: "⏳"}
    lines = ["<b>Группы бота</b>", "✅ работает · ⏳ ждёт решения · ❌ отключена", ""]
    buttons = []
    for chat_id, title, status in chats:
        lines.append(f"{icons[status]} {escape(title)} — <code>{chat_id}</code>")
        short = title if len(title) <= 24 else title[:23] + "…"
        if status == ALLOWED:
            buttons.append([InlineKeyboardButton(text=f"🚫 Отключить «{short}»", callback_data=f"chat:deny:{chat_id}:list")])
        else:
            buttons.append([InlineKeyboardButton(text=f"✅ Разрешить «{short}»", callback_data=f"chat:allow:{chat_id}:list")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


async def request_access(bot: Bot, admin_ids: frozenset[int], chat: Chat, added_by: User | None) -> None:
    """Отправляет администраторам бота запрос на работу в группе."""
    # о добавлении Telegram сообщает двумя событиями — не спрашиваем дважды
    now = time.monotonic()
    if access.status(chat.id) == PENDING and now - access._requested_at.get(chat.id, -1e9) < 30:
        return
    access._requested_at[chat.id] = now
    await access.set(chat.id, PENDING, chat.title)

    lines = ["🔔 <b>Бота добавили в группу</b>", "", f"«{escape(chat.title or '')}»", f"ID: <code>{chat.id}</code>"]
    try:
        lines.append(f"Участников: {await bot.get_chat_member_count(chat.id)}")
    except TelegramAPIError:
        pass
    if added_by is not None:
        who = escape(added_by.full_name) + (f" (@{escape(added_by.username)})" if added_by.username else "")
        lines.append(f"Добавил: {who}")
    lines += ["", "Разрешить боту работать в этой группе?"]

    reached = 0
    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, "\n".join(lines), reply_markup=decision_keyboard(chat.id))
            reached += 1
        except TelegramAPIError as e:
            log.warning("Не удалось отправить запрос администратору %s: %s. "
                        "Он должен открыть бота в личке и нажать «Start»", admin_id, e.message)
    log.info("Запрос на доступ к группе «%s» (%s) отправлен %d админ(ам)", chat.title, chat.id, reached)

    text = "👋 Привет! Чтобы я заработал в этой группе, администратор бота должен разрешить доступ."
    text += " Запрос уже отправлен." if reached else (
        "\n\n⚠️ Не получилось написать администратору бота: пусть он откроет бота в личке, "
        "нажмёт «Start», а потом удалит и снова добавит бота в группу."
    )
    try:
        await bot.send_message(chat.id, text)
    except TelegramAPIError:
        pass
