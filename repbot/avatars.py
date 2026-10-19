"""Загрузка аватарок участников для картинок топа и профиля (с кэшем в памяти).

Бот видит фото профиля только если человек разрешил его показывать всем:
при настройке «Фото профиля: мои контакты / никто» Telegram отдаёт боту «фото нет».
"""

import asyncio
import logging
import time

from aiogram import Bot

log = logging.getLogger(__name__)

CACHE_TTL = 3600       # фото обновляем раз в час
CACHE_TTL_EMPTY = 600  # «фото нет» перепроверяем через 10 минут
TIMEOUT = 10
_cache: dict[int, tuple[float, bytes | None]] = {}


async def _file_id(bot: Bot, user_id: int) -> str | None:
    """file_id маленькой (160x160) аватарки или None, если бот её не видит."""
    photos = await bot.get_user_profile_photos(user_id, limit=1)
    if photos.total_count and photos.photos:
        return photos.photos[0][0].file_id
    # запасной источник — фото из карточки пользователя
    try:
        chat = await bot.get_chat(user_id)
        if chat.photo:
            return chat.photo.small_file_id
    except Exception:
        pass
    return None


async def _fetch(bot: Bot, user_id: int) -> bytes | None:
    file_id = await _file_id(bot, user_id)
    if file_id is None:
        return None
    buf = await bot.download(file_id)
    return buf.read()


async def get_avatar(bot: Bot, user_id: int) -> bytes | None:
    cached = _cache.get(user_id)
    if cached and time.monotonic() - cached[0] < (CACHE_TTL if cached[1] else CACHE_TTL_EMPTY):
        return cached[1]

    error = None
    for _ in range(2):  # одна повторная попытка при сетевой ошибке
        try:
            photo = await asyncio.wait_for(_fetch(bot, user_id), timeout=TIMEOUT)
            break
        except Exception as e:
            error = e
    else:
        log.warning("Аватар %s не загрузился: %s: %s", user_id, type(error).__name__, error)
        return cached[1] if cached else None

    if photo is None:
        log.info("Аватар %s: Telegram не показывает фото боту (скрыто настройками приватности или его нет)", user_id)
    _cache[user_id] = (time.monotonic(), photo)
    return photo


async def get_avatars(bot: Bot, user_ids: list[int]) -> dict[int, bytes | None]:
    photos = await asyncio.gather(*(get_avatar(bot, uid) for uid in user_ids))
    return dict(zip(user_ids, photos))


async def diagnose(bot: Bot, user_id: int) -> list[str]:
    """Пошаговая проверка для /avatar: что Telegram отдаёт по аватарке человека."""
    lines = []
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        lines.append(f"getUserProfilePhotos: фото видно боту — {photos.total_count}")
    except Exception as e:
        lines.append(f"getUserProfilePhotos: ошибка — {type(e).__name__}: {e}")
        photos = None
    try:
        chat = await bot.get_chat(user_id)
        lines.append("getChat: фото " + ("есть" if chat.photo else "нет"))
    except Exception as e:
        lines.append(f"getChat: ошибка — {type(e).__name__}: {e}")
    try:
        file_id = await _file_id(bot, user_id)
        if file_id is None:
            lines.append("Итог: Telegram не показывает фото боту — оно скрыто настройками "
                         "приватности («Фото профиля» → «Все») или его нет")
        else:
            started = time.monotonic()
            data = (await bot.download(file_id)).read()
            lines.append(f"Скачивание: ок, {len(data) // 1024} КБ за {time.monotonic() - started:.1f} с")
            _cache[user_id] = (time.monotonic(), data)
    except Exception as e:
        lines.append(f"Скачивание: ошибка — {type(e).__name__}: {e}")
    return lines
