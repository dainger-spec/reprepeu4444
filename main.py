import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramUnauthorizedError
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeDefault

from repbot.config import ConfigError, load_config
from repbot.db import Database
from repbot.handlers import router
from repbot.middleware import AllowedChatMiddleware, CountMessagesMiddleware
from repbot.tags import check_rights

COMMANDS = [
    BotCommand(command="profile", description="Карточка профиля"),
    BotCommand(command="top", description="Топ-15 по репутации"),
    BotCommand(command="help", description="Справка"),
]

log = logging.getLogger("repbot")


async def connect_db(db: Database, attempts: int = 10) -> None:
    # на хостингах база иногда поднимается позже бота — немного подождём
    for i in range(1, attempts + 1):
        try:
            await db.connect()
            return
        except Exception as e:
            if i == attempts:
                raise
            log.warning("База данных недоступна (%s), повтор через 3 с [%d/%d]", e, i, attempts)
            await asyncio.sleep(3)


async def wait_telegram(bot: Bot, attempts: int = 10):
    # при старте контейнера сеть бывает недоступна пару секунд
    for i in range(1, attempts + 1):
        try:
            return await bot.get_me()
        except TelegramNetworkError as e:
            if i == attempts:
                raise
            log.warning("Нет связи с Telegram (%s), повтор через 5 с [%d/%d]", e, i, attempts)
            await asyncio.sleep(5)


async def log_chat_status(bot: Bot, chat_id: int | None) -> None:
    if chat_id is None:
        log.warning(
            "ALLOWED_CHAT_ID не задан — бот не работает ни в одной группе. Добавьте бота в свою группу, "
            "напишите там /chatid и укажите полученный ID в настройках хостинга"
        )
        return
    try:
        for ok, text in await check_rights(bot, chat_id):
            (log.info if ok else log.warning)("[рабочий чат] %s", text)
    except TelegramAPIError as e:
        log.warning("Нет доступа к чату ALLOWED_CHAT_ID=%s (%s) — бот добавлен в эту группу?", chat_id, e.message)


async def main() -> None:
    config = load_config()

    db = Database(config.database_url, config.db_path)
    await connect_db(db)
    log.info("База данных: %s", "PostgreSQL" if config.database_url else config.db_path)

    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(db=db, config=config)
    dp.update.outer_middleware(AllowedChatMiddleware())
    dp.message.outer_middleware(CountMessagesMiddleware())
    dp.include_router(router)

    try:
        me = await wait_telegram(bot)
        log.info("Бот запущен: @%s", me.username)
        # если раньше был настроен webhook, polling не заработает — снимаем его
        await bot.delete_webhook()
        await bot.set_my_commands(COMMANDS, scope=BotCommandScopeDefault())
        await bot.set_my_commands(COMMANDS, scope=BotCommandScopeAllGroupChats())
        await log_chat_status(bot, config.allowed_chat_id)
        # chat_member нужен, чтобы точно знать дату вступления в группу
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await db.close()
        await bot.session.close()


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(main())
    except ConfigError as e:
        log.error("%s", e)
        sys.exit(1)
    except TelegramUnauthorizedError:
        log.error("Telegram отклонил BOT_TOKEN — проверьте токен в @BotFather и в настройках хостинга")
        sys.exit(1)
    except TelegramNetworkError as e:
        log.error("Не удалось подключиться к Telegram: %s", e)
        sys.exit(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
