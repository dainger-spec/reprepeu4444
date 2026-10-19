import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.types import Chat, Message, Update, User

from .access import access, request_access
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
        if (
            access.is_allowed(event.chat.id)
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
    """Бот работает только в разрешённых группах (см. access.py).

    Новая группа → запрос администратору бота с кнопками, пока решения нет — бот молчит.
    В личке отвечает как обычно. Команда /chatid администратора бота работает в любой группе.
    """

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        chat: Chat | None = data.get("event_chat")
        if chat is None or chat.type == "private":
            return await handler(event, data)
        if chat.type not in ("group", "supergroup"):
            return None  # каналы не обслуживаем

        msg = event.message
        if msg is not None and msg.migrate_to_chat_id:
            await access.migrate(chat.id, msg.migrate_to_chat_id, chat.title)
        if msg is not None and msg.migrate_from_chat_id:
            await access.migrate(msg.migrate_from_chat_id, chat.id, chat.title)

        if access.is_allowed(chat.id):
            access.remember_title(chat.id, chat.title)
            return await handler(event, data)

        config: Config = data["config"]
        user: User | None = data.get("event_from_user")
        if (
            msg is not None
            and user is not None
            and user.id in config.admin_ids
            and msg.text
            and msg.text.split()[0].split("@")[0].lower() == "/chatid"
        ):
            return await handler(event, data)

        bot: Bot = data["bot"]
        mcm = event.my_chat_member
        if mcm is not None:
            added = (mcm.new_chat_member.status in ("member", "administrator")
                     and mcm.old_chat_member.status in ("left", "kicked"))
            if added:
                # каждое новое добавление — новый запрос, даже если раньше отклоняли
                await request_access(bot, config.admin_ids, chat, mcm.from_user)
            return None

        if access.status(chat.id) is None:
            # бот уже был в группе, но о ней ещё не спрашивали
            await request_access(bot, config.admin_ids, chat, None)
        return None
