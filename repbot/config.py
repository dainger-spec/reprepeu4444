import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent


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


def _parse_ids(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.replace(" ", "").split(",") if x)


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    database_url: str | None
    db_path: Path
    daily_rep_limit: int
    tag_format: str
    tz: ZoneInfo


def load_config() -> Config:
    _load_dotenv(BASE_DIR / ".env")

    token = os.getenv("BOT_TOKEN", "")
    if not token:
        raise RuntimeError("Не задан BOT_TOKEN (переменная окружения или файл .env)")

    db_path = Path(os.getenv("DB_PATH", BASE_DIR / "data" / "repbot.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)

    return Config(
        bot_token=token,
        admin_ids=_parse_ids(os.getenv("ADMIN_IDS", "1457986905")),
        database_url=os.getenv("DATABASE_URL") or None,
        db_path=db_path,
        daily_rep_limit=int(os.getenv("DAILY_REP_LIMIT", "3")),
        tag_format=os.getenv("TAG_FORMAT", "реп{rep}"),
        tz=ZoneInfo(os.getenv("TZ_NAME", "Europe/Moscow")),
    )
