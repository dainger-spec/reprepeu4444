import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Chat, Message, Update, User

from .config import Config
from .db import Database
from .importer import importer

log = logging.getLogger(__name__)

# служебные сообщения не считаем как написанные
SERVICE_TYPES = {
    "new_chat_members", "left_chat_member", "new_chat_title", "new_chat_photo",
    "delete_chat_photo", "group_chat_created", "supergroup_chat_created",
    "migrate_to_chat_id", "migrate_from_chat_id", "pinned_message",
    "forum_topic_created", "forum_topic_edited", "forum_topic_closed",
    "forum_topic_reopened", "general_forum_topic_hidden", "general_forum_topic_unhidden",
    "video_chat_scheduled", "video_chat_started", "video_chat_ended",
    "video_chat_participants_invited", "message_auto_delete_timer_changed",
    "boost_added", "chat_background_set",
}


class CountMessagesMiddleware(BaseMiddleware):
    """Ведёт профиль участника и считает его сообщения в группе."""

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user
        config: Config = data["config"]
        if (
            event.chat.id in config.allowed_chat_ids
            and user is not None
            and not user.is_bot
            and event.sender_chat is None
        ):
            db: Database = data["db"]
            await db.touch(
                event.chat.id,
                user.id,
                user.username,
                user.full_name,
                count_message=event.content_type not in SERVICE_TYPES,
                joined_at=int(event.date.timestamp()),
            )
            await importer.match(data["bot"], event.chat.id, user.id, user.username)
        return await handler(event, data)


class AllowedChatMiddleware(BaseMiddleware):
    """Бот работает только в своих группах (ALLOWED_CHAT_ID), из остальных выходит.

    В личке отвечает как обычно (/start, /help). Команда /chatid администратора
    бота работает в любой группе — чтобы узнать ID для настройки.
    """

    def __init__(self) -> None:
        self._warned: set[int] = set()

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        chat: Chat | None = data.get("event_chat")
        if chat is None or chat.type == "private":
            return await handler(event, data)

        config: Config = data["config"]
        msg = event.message
        if chat.id in config.allowed_chat_ids:
            if msg is not None and msg.migrate_to_chat_id:
                log.error(
                    "Группа %s стала супергруппой и сменила ID на %s — замените его в ALLOWED_CHAT_ID "
                    "в настройках хостинга и перезапустите бота", chat.id, msg.migrate_to_chat_id,
                )
            return await handler(event, data)

        user: User | None = data.get("event_from_user")
        if (
            msg is not None
            and user is not None
            and user.id in config.admin_ids
            and msg.text
            and msg.text.split()[0].split("@")[0].lower() == "/chatid"
        ):
            return await handler(event, data)

        mcm = event.my_chat_member
        if mcm is not None and mcm.new_chat_member.status in ("member", "administrator"):
            log.warning("Бота добавили в чат «%s» (id %s)", chat.title, chat.id)
            if config.allowed_chat_ids:
                # чужой чат — вежливо выходим
                bot: Bot = data["bot"]
                try:
                    await bot.send_message(chat.id, "Этот бот работает только в своей группе.")
                    await bot.leave_chat(chat.id)
                except TelegramAPIError as e:
                    log.warning("Не удалось выйти из чата %s: %s", chat.id, e)
                return None

        if chat.id not in self._warned:
            self._warned.add(chat.id)
            if not config.allowed_chat_ids:
                log.warning(
                    "ALLOWED_CHAT_ID не задан — бот нигде не работает. Если чат «%s» ваш, укажите "
                    "ALLOWED_CHAT_ID=%s в настройках хостинга и перезапустите бота", chat.title, chat.id,
                )
            else:
                log.warning(
                    "Чат «%s» (id %s) игнорируется: бот работает только в чатах %s",
                    chat.title, chat.id, ", ".join(map(str, sorted(config.allowed_chat_ids))),
                )
        return None
