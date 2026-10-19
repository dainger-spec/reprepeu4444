import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeDefault

from repbot.config import load_config
from repbot.db import Database
from repbot.handlers import router
from repbot.middleware import CountMessagesMiddleware

COMMANDS = [
    BotCommand(command="profile", description="Карточка профиля"),
    BotCommand(command="top", description="Топ-15 по репутации"),
    BotCommand(command="help", description="Справка"),
]


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()

    db = Database(config.database_url, config.db_path)
    await db.connect()
    logging.info("База данных: %s", "PostgreSQL" if config.database_url else config.db_path)

    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(db=db, config=config)
    dp.message.outer_middleware(CountMessagesMiddleware())
    dp.include_router(router)

    await bot.set_my_commands(COMMANDS, scope=BotCommandScopeDefault())
    await bot.set_my_commands(COMMANDS, scope=BotCommandScopeAllGroupChats())

    try:
        # chat_member нужен, чтобы точно знать дату вступления в группу
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
