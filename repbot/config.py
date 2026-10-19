import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BASE_DIR = Path(__file__).resolve().parent.parent
TOKEN_RE = re.compile(r"^\d+:[\w-]{30,}$")

log = logging.getLogger(__name__)


class ConfigError(Exception):
    pass


def _load_dotenv(path: Path) -> None:
    """Минимальная загрузка .env без внешних зависимостей."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _default_db_path() -> Path:
    # Railway сам сообщает, куда смонтирован подключённый volume
    volume = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if volume:
        return Path(volume) / "repbot.db"
    return BASE_DIR / "data" / "repbot.db"


def _ephemeral_host() -> bool:
    if os.getenv("RAILWAY_ENVIRONMENT") and not os.getenv("RAILWAY_VOLUME_MOUNT_PATH"):
        return True
    return bool(os.getenv("RENDER") or os.getenv("DYNO")) and not os.getenv("DB_PATH")


def _parse_ids(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.replace(" ", "").split(",") if x)


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    allowed_chat_ids: frozenset[int]  # пусто — бот не работает ни в одной группе
    database_url: str | None
    db_path: Path
    daily_rep_limit: int
    tag_format: str
    tz: ZoneInfo


def load_config() -> Config:
    _load_dotenv(BASE_DIR / ".env")

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError(
            "Не задан токен бота. Добавьте переменную окружения BOT_TOKEN "
            "в настройках хостинга (Variables / Environment) или в файл .env"
        )
    if not TOKEN_RE.match(token):
        raise ConfigError("BOT_TOKEN выглядит неправильно — скопируйте его целиком из @BotFather")

    database_url = os.getenv("DATABASE_URL", "").strip() or None
    db_path = Path(os.getenv("DB_PATH") or _default_db_path())
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if database_url is None and _ephemeral_host():
        log.warning(
            "База SQLite: %s. Если к сервису не подключён постоянный диск (volume) с этой папкой, "
            "данные пропадут при перезапуске — подключите volume или задайте DATABASE_URL.", db_path
        )

    try:
        admin_ids = _parse_ids(os.getenv("ADMIN_IDS", "1457986905"))
    except ValueError:
        raise ConfigError("ADMIN_IDS должен быть списком числовых ID через запятую") from None
    try:
        allowed_chat_ids = _parse_ids(os.getenv("ALLOWED_CHAT_ID", ""))
    except ValueError:
        raise ConfigError(
            "ALLOWED_CHAT_ID — ID групп через запятую, например -1001234567890,-1009876543210 "
            "(узнать: /chatid в группе)"
        ) from None

    try:
        daily_rep_limit = int(os.getenv("DAILY_REP_LIMIT", "3"))
    except ValueError:
        raise ConfigError("DAILY_REP_LIMIT должен быть числом") from None
    try:
        tz = ZoneInfo(os.getenv("TZ_NAME", "Europe/Moscow"))
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError("Неизвестный часовой пояс TZ_NAME, пример: Europe/Moscow") from None

    return Config(
        bot_token=token,
        admin_ids=admin_ids,
        allowed_chat_ids=allowed_chat_ids,
        database_url=database_url,
        db_path=db_path,
        daily_rep_limit=daily_rep_limit,
        tag_format=os.getenv("TAG_FORMAT", "реп{rep}"),
        tz=tz,
    )
