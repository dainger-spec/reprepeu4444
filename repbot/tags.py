"""Серая плашка (тег участника) с репутацией.

Используется метод Bot API setChatMemberTag (Bot API 9.5+): он ставит тег
обычному участнику без выдачи прав администратора. У бота должно быть право
администратора «Управление тегами» (can_manage_tags).
Для администраторов группы теги не работают — им можно поставить
«звание» (custom title), но только если этого админа назначил сам бот.
"""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

log = logging.getLogger(__name__)

MAX_TAG_LEN = 16


def make_tag(fmt: str, rep: int) -> str:
    return fmt.format(rep=rep)[:MAX_TAG_LEN]


async def apply_tag(bot: Bot, chat_id: int, user_id: int, tag: str) -> bool:
    try:
        await bot.set_chat_member_tag(chat_id=chat_id, user_id=user_id, tag=tag)
        return True
    except TelegramAPIError as e:
        tag_error = e

    # участник может быть администратором — пробуем звание
    try:
        await bot.set_chat_administrator_custom_title(
            chat_id=chat_id, user_id=user_id, custom_title=tag
        )
        return True
    except TelegramAPIError as e:
        log.warning(
            "Не удалось поставить плашку %r пользователю %s в чате %s. "
            "Тег: %s. Звание: %s. Проверьте права бота командой /check",
            tag, user_id, chat_id, tag_error.message, e.message,
        )
        return False


async def check_rights(bot: Bot, chat_id: int) -> list[tuple[bool, str]]:
    """Проверка, хватает ли боту прав в чате. Возвращает список (ок?, текст)."""
    me = await bot.get_me()
    member = await bot.get_chat_member(chat_id, me.id)
    chat = await bot.get_chat(chat_id)
    is_admin = member.status == "administrator"
    can_tags = bool(getattr(member, "can_manage_tags", False))
    perms = chat.permissions
    members_edit_tags = bool(perms and perms.can_edit_tag)

    return [
        (chat.type == "supergroup" or chat.type == "group", f"Чат: {chat.title} ({chat.type})"),
        (is_admin, "Бот — администратор группы" if is_admin
         else "Бот НЕ администратор — сделайте его админом"),
        (can_tags, "Есть право «Управление тегами» — плашки ставятся" if can_tags
         else "НЕТ права «Управление тегами» — плашки ставиться не будут"),
        (not members_edit_tags, "Участники не могут менять свой тег" if not members_edit_tags
         else "Участники могут сами менять свой тег — запретите в разрешениях группы, иначе плашку можно подделать"),
    ]
