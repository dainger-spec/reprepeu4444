"""Отрисовка таблицы топа репутации в PNG."""

import io
import math
import unicodedata
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from .config import BASE_DIR
from .db import Member

FONTS = BASE_DIR / "fonts"
FALLBACK_FONT = FONTS / "DejaVuSans.ttf"  # для символов, которых нет в Inter

S = 2  # рисуем в двойном разрешении, чтобы картинка была чёткой
WIDTH = 900
PAD = 36
HEADER_H = 128
COLS_H = 40
ROW_H = 64
ROW_GAP = 6
AVATAR = 40

BG = (18, 20, 27)
CARD = (29, 32, 42)
CARD_ALT = (34, 38, 50)
TEXT = (240, 242, 247)
MUTED = (134, 141, 158)
ACCENT = (110, 168, 255)
STAR_ALPHA = 38  # прозрачность звёзд на фоне (0 — не видно, 255 — сплошные)
CARD_ALPHA = 215  # плашки строк чуть просвечивают, чтобы звёзды были видны позади
MEDALS = {1: (255, 196, 61), 2: (200, 208, 220), 3: (222, 145, 86)}
# цвета «пустых» аватарок как в Telegram
AVATAR_COLORS = [
    (229, 86, 86), (240, 150, 60), (150, 110, 230), (80, 185, 95),
    (70, 175, 200), (80, 145, 230), (225, 100, 160),
]


@lru_cache(maxsize=None)
def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"Inter-{name}.ttf"), size * S)


@lru_cache(maxsize=None)
def _notdef(f: ImageFont.FreeTypeFont) -> bytes:
    return bytes(f.getmask("\u0378"))  # неназначенный код — всегда «нет глифа»


@lru_cache(maxsize=4096)
def _has_glyph(f: ImageFont.FreeTypeFont, ch: str) -> bool:
    return ch.isspace() or bytes(f.getmask(ch)) != _notdef(f)


@lru_cache(maxsize=None)
def _fallback(px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FALLBACK_FONT), px)


def _runs(text: str, f: ImageFont.FreeTypeFont) -> list[tuple[str, ImageFont.FreeTypeFont]]:
    """Разбивает текст на куски: что есть в Inter — им, остальное — запасным шрифтом."""
    fb = _fallback(f.size)
    runs: list[tuple[str, ImageFont.FreeTypeFont]] = []
    for ch in text:
        cur = f if _has_glyph(f, ch) else fb
        if runs and runs[-1][1] is cur:
            runs[-1] = (runs[-1][0] + ch, cur)
        else:
            runs.append((ch, cur))
    return runs


def _width(d: ImageDraw.ImageDraw, text: str, f) -> float:
    return sum(d.textlength(t, font=rf) for t, rf in _runs(text, f))


def _draw_name(d: ImageDraw.ImageDraw, x: float, cy: float, text: str, f, fill) -> None:
    for t, rf in _runs(text, f):
        d.text((x, cy), t, font=rf, fill=fill, anchor="lm")
        x += d.textlength(t, font=rf)


def _clean(name: str) -> str:
    """Убирает эмодзи, служебные символы и то, чего нет ни в одном из шрифтов."""
    main = font("Medium", 22)
    fb = _fallback(main.size)
    out = []
    for ch in name:
        if ord(ch) > 0xFFFF or unicodedata.category(ch) in ("So", "Cs", "Co", "Cn", "Mn", "Cf"):
            continue
        if not (_has_glyph(main, ch) or _has_glyph(fb, ch)):
            continue
        out.append(ch)
    return " ".join("".join(out).split())


def _display(m: Member) -> str:
    return _clean(m.display_name) or (f"@{m.username}" if m.username else f"id{m.user_id}")


def _fit(d: ImageDraw.ImageDraw, text: str, f, max_w: float) -> str:
    if _width(d, text, f) <= max_w:
        return text
    while text and _width(d, text + "…", f) > max_w:
        text = text[:-1]
    return text.rstrip() + "…"


def _circle_mask(size: int) -> Image.Image:
    big = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(big).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    return big.resize((size, size), Image.LANCZOS)


def _avatar(m: Member, photo: bytes | None, size: int) -> Image.Image:
    if photo:
        try:
            img = Image.open(io.BytesIO(photo)).convert("RGB")
            side = min(img.size)
            left, top = (img.width - side) // 2, (img.height - side) // 2
            return img.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
        except Exception:
            pass
    # нет фото — кружок с первой буквой, как в Telegram
    color = AVATAR_COLORS[m.user_id % len(AVATAR_COLORS)]
    img = Image.new("RGB", (size, size), color)
    letter = _display(m)[:1].upper()
    f = font("SemiBold", AVATAR // 2 - 2)
    if not _has_glyph(f, letter):
        f = _fallback(f.size)
    ImageDraw.Draw(img).text((size / 2, size / 2), letter, font=f, fill=(255, 255, 255), anchor="mm")
    return img


def _star(radius: int) -> Image.Image:
    """Белая пятиконечная звезда (как на флаге ЕС) с полупрозрачностью."""
    k = 4  # сглаживание
    size = radius * 2 * k
    big = Image.new("L", (size, size), 0)
    points = []
    for j in range(10):
        r = radius * k * (1 if j % 2 == 0 else 0.382)
        a = math.radians(-90 + j * 36)
        points.append((size / 2 + r * math.cos(a), size / 2 + r * math.sin(a)))
    ImageDraw.Draw(big).polygon(points, fill=STAR_ALPHA)
    star = Image.new("RGBA", (radius * 2, radius * 2), (255, 255, 255, 0))
    star.putalpha(big.resize((radius * 2, radius * 2), Image.LANCZOS))
    return star


def _draw_stars(img: Image.Image) -> Image.Image:
    """Круг из 12 звёзд по центру картинки, пропорции как на флаге ЕС."""
    w, h = img.size
    ring = min(w, h) * 0.36
    star = _star(int(ring / 6))
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    for i in range(12):
        a = math.radians(i * 30)
        x = w / 2 + ring * math.sin(a) - star.width / 2
        y = h / 2 - ring * math.cos(a) - star.height / 2
        layer.alpha_composite(star, (int(x), int(y)))
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def render_top(chat_title: str, members: list[Member], avatars: dict[int, bytes | None]) -> bytes:
    rows = max(len(members), 1)
    height = HEADER_H + COLS_H + rows * ROW_H + PAD
    img = Image.new("RGB", (WIDTH * S, height * S), BG)
    d = ImageDraw.Draw(img)

    def box(x0, y0, x1, y1, r, fill):
        d.rounded_rectangle((x0 * S, y0 * S, x1 * S, y1 * S), r * S, fill=fill)

    # звёзды — на самом заднем плане, поверх них полупрозрачные плашки строк, затем всё остальное
    img = _draw_stars(img)
    cards = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(cards)
    y0 = HEADER_H + COLS_H
    for i in range(1, rows + 1):
        y = y0 + (i - 1) * ROW_H
        box(PAD, y, WIDTH - PAD, y + ROW_H - ROW_GAP, 14, (*(CARD if i % 2 else CARD_ALT), CARD_ALPHA))
    img = Image.alpha_composite(img.convert("RGBA"), cards).convert("RGB")
    d = ImageDraw.Draw(img)

    def text(x, y, t, f, fill, anchor="la"):
        d.text((x * S, y * S), t, font=f, fill=fill, anchor=anchor)

    text(PAD, 34, "Топ-15 по репутации", font("Bold", 36), TEXT)
    sub = font("Medium", 20)
    _draw_name(d, PAD * S, 92 * S, _fit(d, _clean(chat_title), sub, (WIDTH - 2 * PAD) * S), sub, MUTED)

    x_place = PAD + 30
    x_avatar = PAD + 60
    x_name = x_avatar + AVATAR + 14
    x_rep_r, x_msg_r = WIDTH - PAD - 170, WIDTH - PAD - 22

    y = HEADER_H
    col = font("SemiBold", 14)
    text(x_place, y + 12, "#", col, MUTED, "ma")
    text(x_name, y + 12, "УЧАСТНИК", col, MUTED)
    text(x_rep_r, y + 12, "РЕПУТАЦИЯ", col, MUTED, "ra")
    text(x_msg_r, y + 12, "СООБЩЕНИЙ", col, MUTED, "ra")
    y += COLS_H

    if not members:
        text(WIDTH / 2, y + (ROW_H - ROW_GAP) / 2, "Пока никто не получил репутацию",
             font("Medium", 22), MUTED, "mm")

    mask = _circle_mask(AVATAR * S)
    name_max_w = (x_rep_r - 130 - x_name) * S
    for i, m in enumerate(members, start=1):
        cy = y + (ROW_H - ROW_GAP) / 2

        if i in MEDALS:
            r = 15
            medal = Image.new("RGB", (r * 2 * S, r * 2 * S), MEDALS[i])
            img.paste(medal, (int((x_place - r) * S), int((cy - r) * S)), _circle_mask(r * 2 * S))
            text(x_place, cy, str(i), font("Bold", 16), BG, "mm")
        else:
            text(x_place, cy, str(i), font("SemiBold", 18), MUTED, "mm")

        av = _avatar(m, avatars.get(m.user_id), AVATAR * S)
        img.paste(av, (x_avatar * S, int((cy - AVATAR / 2) * S)), mask)

        f_name = font("SemiBold" if i <= 3 else "Medium", 22)
        _draw_name(d, x_name * S, cy * S, _fit(d, _display(m), f_name, name_max_w), f_name, TEXT)
        text(x_rep_r, cy, str(m.rep), font("Bold", 22), ACCENT, "rm")
        text(x_msg_r, cy, str(m.messages), font("Medium", 20), MUTED, "rm")
        y += ROW_H

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
