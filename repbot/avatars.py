"""Загрузка аватарок участников для картинки топа (с кэшем в памяти)."""

import asyncio
import logging
import time

from aiogram import Bot

log = logging.getLogger(__name__)

CACHE_TTL = 3600
_cache: dict[int, tuple[float, bytes | None]] = {}


async def _fetch(bot: Bot, user_id: int) -> bytes | None:
    photos = await bot.get_user_profile_photos(user_id, limit=1)
    if not photos.total_count:
        return None
    smallest = photos.photos[0][0]  # 160x160 — для маленькой аватарки хватает
    buf = await bot.download(smallest.file_id)
    return buf.read()


async def get_avatar(bot: Bot, user_id: int) -> bytes | None:
    cached = _cache.get(user_id)
    if cached and time.monotonic() - cached[0] < CACHE_TTL:
        return cached[1]
    try:
        photo = await asyncio.wait_for(_fetch(bot, user_id), timeout=5)
    except Exception as e:
        log.debug("Не удалось загрузить аватар %s: %s", user_id, e)
        return cached[1] if cached else None
    _cache[user_id] = (time.monotonic(), photo)
    return photo


async def get_avatars(bot: Bot, user_ids: list[int]) -> dict[int, bytes | None]:
    photos = await asyncio.gather(*(get_avatar(bot, uid) for uid in user_ids))
    return dict(zip(user_ids, photos))
