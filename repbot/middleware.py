from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

from .db import Database

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
            event.chat.type in ("group", "supergroup")
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
        return await handler(event, data)
