"""Отрисовка таблицы топа репутации в PNG."""

import io
import unicodedata

from PIL import Image, ImageDraw, ImageFont

from .config import BASE_DIR
from .db import Member

FONT_REGULAR = BASE_DIR / "fonts" / "DejaVuSans.ttf"
FONT_BOLD = BASE_DIR / "fonts" / "DejaVuSans-Bold.ttf"

WIDTH = 900
PAD = 40
HEADER_H = 130
COLS_H = 44
ROW_H = 58
FOOTER_H = 0

BG = (24, 26, 33)
CARD = (33, 36, 46)
CARD_ALT = (38, 42, 54)
TEXT = (236, 238, 243)
MUTED = (140, 146, 162)
ACCENT = (120, 170, 255)
MEDALS = {1: (255, 196, 61), 2: (196, 204, 216), 3: (214, 140, 82)}


def _clean(name: str) -> str:
    """Убирает эмодзи и прочие символы, которых нет в шрифте."""
    out = []
    for ch in name:
        if ord(ch) > 0xFFFF or unicodedata.category(ch) in ("So", "Cs", "Co", "Cn", "Mn", "Cf"):
            continue
        out.append(ch)
    return " ".join("".join(out).split()) or "Без имени"


def _fit(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def render_top(chat_title: str, members: list[Member]) -> bytes:
    rows = max(len(members), 1)
    height = HEADER_H + COLS_H + rows * ROW_H + PAD + FOOTER_H
    img = Image.new("RGB", (WIDTH, height), BG)
    d = ImageDraw.Draw(img)

    f_title = ImageFont.truetype(str(FONT_BOLD), 38)
    f_sub = ImageFont.truetype(str(FONT_REGULAR), 22)
    f_col = ImageFont.truetype(str(FONT_BOLD), 18)
    f_row = ImageFont.truetype(str(FONT_REGULAR), 26)
    f_row_b = ImageFont.truetype(str(FONT_BOLD), 26)
    f_place = ImageFont.truetype(str(FONT_BOLD), 22)

    d.text((PAD, 34), "Топ-15 по репутации", font=f_title, fill=TEXT)
    d.text((PAD, 84), _fit(d, _clean(chat_title), f_sub, WIDTH - 2 * PAD), font=f_sub, fill=MUTED)

    x_place, x_name = PAD + 36, PAD + 84
    x_rep_r, x_msg_r = WIDTH - PAD - 190, WIDTH - PAD - 20
    y = HEADER_H
    d.text((x_place, y + 12), "#", font=f_col, fill=MUTED, anchor="ma")
    d.text((x_name, y + 12), "УЧАСТНИК", font=f_col, fill=MUTED)
    d.text((x_rep_r, y + 12), "РЕП", font=f_col, fill=MUTED, anchor="ra")
    d.text((x_msg_r, y + 12), "СООБЩЕНИЙ", font=f_col, fill=MUTED, anchor="ra")
    y += COLS_H

    if not members:
        d.rounded_rectangle((PAD, y, WIDTH - PAD, y + ROW_H - 6), 12, fill=CARD)
        d.text((WIDTH // 2, y + (ROW_H - 6) // 2), "Пока никто не получил репутацию",
               font=f_row, fill=MUTED, anchor="mm")

    name_max_w = x_rep_r - 120 - x_name
    for i, m in enumerate(members, start=1):
        box = (PAD, y, WIDTH - PAD, y + ROW_H - 6)
        d.rounded_rectangle(box, 12, fill=CARD if i % 2 else CARD_ALT)
        cy = y + (ROW_H - 6) // 2

        if i in MEDALS:
            r = 18
            cx = x_place
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=MEDALS[i])
            d.text((cx, cy), str(i), font=f_place, fill=BG, anchor="mm")
        else:
            d.text((x_place, cy), str(i), font=f_place, fill=MUTED, anchor="mm")

        name = _fit(d, _clean(m.display_name), f_row_b if i <= 3 else f_row, name_max_w)
        d.text((x_name, cy), name, font=f_row_b if i <= 3 else f_row, fill=TEXT, anchor="lm")
        d.text((x_rep_r, cy), str(m.rep), font=f_row_b, fill=ACCENT, anchor="rm")
        d.text((x_msg_r, cy), str(m.messages), font=f_row, fill=MUTED, anchor="rm")
        y += ROW_H

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
