import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramUnauthorizedError
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeChat, BotCommandScopeDefault

from repbot import __version__
from repbot.access import access
from repbot.config import BASE_DIR, ConfigError, load_config
from repbot.db import Database
from repbot.handlers import router
from repbot.importer import importer
from repbot.middleware import AllowedChatMiddleware, CountMessagesMiddleware
from repbot.tags import check_rights

COMMANDS = [
    BotCommand(command="profile", description="Карточка профиля"),
    BotCommand(command="top", description="Топ-15 по репутации"),
    BotCommand(command="help", description="Справка"),
]
ADMIN_COMMANDS = [BotCommand(command="chats", description="Группы бота: разрешить / отключить")]

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


async def log_chat_status(bot: Bot, chat_ids: list[int]) -> None:
    if not chat_ids:
        log.warning(
            "Нет разрешённых групп. Добавьте бота в группу — администратору бота в личку придёт "
            "запрос с кнопками. Администратор должен заранее открыть бота в личке и нажать «Start»"
        )
        return
    for chat_id in chat_ids:
        try:
            for ok, text in await check_rights(bot, chat_id):
                (log.info if ok else log.warning)("[чат %s] %s", chat_id, text)
        except TelegramAPIError as e:
            log.warning("Нет доступа к группе %s (%s) — бот добавлен в эту группу?", chat_id, e.message)


async def main() -> None:
    config = load_config()

    db = Database(config.database_url, config.db_path)
    await connect_db(db)
    log.info("База данных: %s", "PostgreSQL" if config.database_url else config.db_path)
    await importer.load(db, config.tag_format)
    await access.load(db, config.allowed_chat_ids)

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
        for admin_id in config.admin_ids:
            try:
                await bot.set_my_commands(COMMANDS + ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
            except TelegramAPIError:
                pass  # админ ещё не открывал бота в личке
        await log_chat_status(bot, access.allowed_ids())
        # chat_member нужен, чтобы точно знать дату вступления в группу
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await db.close()
        await bot.session.close()


def setup_logging() -> None:
    # stdout, а не stderr: некоторые хостинги показывают только его.
    # Плюс файл data/bot.log — на случай, если хостинг не показывает логи вовсе.
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        log_dir = BASE_DIR / "data"
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_dir / "bot.log", maxBytes=1_000_000, backupCount=1, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers
    )


def run() -> None:
    setup_logging()
    log.info("Запуск РЕП версии %s (Python %s)", __version__, sys.version.split()[0])
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
    except Exception:
        log.exception("Бот остановился из-за ошибки")
        sys.exit(1)


if __name__ == "__main__":
    run()
