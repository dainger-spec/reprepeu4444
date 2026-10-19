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
        log.debug("setChatMemberTag failed for %s in %s: %s", user_id, chat_id, e)

    # участник может быть администратором — пробуем звание
    try:
        await bot.set_chat_administrator_custom_title(
            chat_id=chat_id, user_id=user_id, custom_title=tag
        )
        return True
    except TelegramAPIError as e:
        log.warning("Не удалось поставить плашку %r пользователю %s в чате %s: %s",
                    tag, user_id, chat_id, e)
        return False
