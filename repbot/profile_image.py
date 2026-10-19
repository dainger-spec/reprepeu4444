"""Карточка профиля (/profile) картинкой — в том же стиле, что и топ."""

import io
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

from .db import Member
from .top_image import (
    ACCENT, BG, CARD, CARD_ALPHA, MUTED, PAD, S, TEXT, WIDTH,
    _avatar, _circle_mask, _clean, _display, _draw_name, _draw_stars, _fit, _width, font,
)

HEIGHT = 480
AVATAR = 132
TILE_BG = (38, 42, 54)
TAG_BG = (58, 62, 74)  # серая плашка, как в Telegram
BANNED = (229, 86, 86)


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def render_profile(
    member: Member,
    place: int,
    total: int,
    tag: str,
    chat_title: str,
    photo: bytes | None,
    tz: ZoneInfo,
) -> bytes:
    img = Image.new("RGB", (WIDTH * S, HEIGHT * S), BG)
    img = _draw_stars(img)

    # полупрозрачная карточка поверх звёзд
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.rounded_rectangle((PAD * S, 64 * S, (WIDTH - PAD) * S, (HEIGHT - PAD) * S), 22 * S, fill=(*CARD, CARD_ALPHA))
    img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
    d = ImageDraw.Draw(img)

    def text(x, y, t, f, fill, anchor="la"):
        d.text((x * S, y * S), t, font=f, fill=fill, anchor=anchor)

    def box(x0, y0, x1, y1, r, fill):
        d.rounded_rectangle((x0 * S, y0 * S, x1 * S, y1 * S), r * S, fill=fill)

    # шапка: «Профиль · Название группы»
    head = font("SemiBold", 18)
    _draw_name(d, PAD * S, 34 * S, _fit(d, "Профиль · " + _clean(chat_title), head, (WIDTH - 2 * PAD) * S), head, MUTED)

    # аватарка
    ax, ay = PAD + 30, 92
    img.paste(_avatar(member, photo, AVATAR * S), (ax * S, ay * S), _circle_mask(AVATAR * S))

    # имя, ник и плашка
    x = ax + AVATAR + 30
    max_w = (WIDTH - PAD - 30 - x) * S
    f_name = font("Bold", 34)
    _draw_name(d, x * S, (ay + 30) * S, _fit(d, _display(member), f_name, max_w), f_name, TEXT)
    sub = f"@{member.username}" if member.username else f"ID {member.user_id}"
    f_sub = font("Medium", 20)
    _draw_name(d, x * S, (ay + 70) * S, _fit(d, sub, f_sub, max_w), f_sub, MUTED)

    f_tag = font("SemiBold", 17)
    if member.banned:
        label, bg = "Доступ к боту заблокирован", BANNED
    else:
        label, bg = tag, TAG_BG
    if label:
        w = _width(d, label, f_tag) / S + 24
        box(x, ay + 96, x + w, ay + 126, 15, bg)
        text(x + 12, ay + 111, label, f_tag, TEXT, "lm")

    # три плитки: репутация, сообщения, в группе с
    days = max(0, (int(time.time()) - member.joined_at) // 86400)
    tiles = [
        ("РЕПУТАЦИЯ", str(member.rep), ACCENT, "" if member.banned else f"место {place} из {total}"),
        ("СООБЩЕНИЙ", str(member.messages), TEXT, ""),
        ("В ГРУППЕ С", datetime.fromtimestamp(member.joined_at, tz).strftime("%d.%m.%Y"), TEXT,
         f"{days} {_plural(days, 'день', 'дня', 'дней')}"),
    ]
    gap = 16
    left, right = PAD + 30, WIDTH - PAD - 30
    tw = (right - left - gap * 2) / 3
    ty0, ty1 = 262, HEIGHT - PAD - 26
    for i, (label, value, color, note) in enumerate(tiles):
        tx = left + i * (tw + gap)
        box(tx, ty0, tx + tw, ty1, 16, TILE_BG)
        text(tx + 20, ty0 + 20, label, font("SemiBold", 14), MUTED)
        f_val = font("Bold", 40 if len(value) <= 6 else 30)
        text(tx + 20, ty0 + 72, value, f_val, color, "lm")
        if note:
            text(tx + 20, ty1 - 20, note, font("Medium", 16), MUTED, "ls")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
