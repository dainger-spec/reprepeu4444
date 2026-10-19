import asyncio
import time
from datetime import datetime
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import JOIN_TRANSITION, ChatMemberUpdatedFilter, Command, CommandObject, CommandStart
from aiogram.types import (
    BufferedInputFile,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

from .avatars import get_avatars
from .config import Config
from .db import Database, Member
from .tags import apply_tag, make_tag
from .top_image import render_top

router = Router()
group_only = F.chat.type.in_({"group", "supergroup"})

# +rep от одного человека не должны обрабатываться параллельно, иначе можно обойти лимит
rep_lock = asyncio.Lock()

HELP_TEXT = (
    "<b>РЕП — бот репутации</b>\n\n"
    "Добавьте меня в группу — там я веду профили и репутацию: сколько написано "
    "сообщений, когда человек вступил и какая у него репутация.\n\n"
    "Чтобы повысить репутацию, ответьте на сообщение участника словами "
    "<code>+rep</code> или <code>+реп</code>. Можно выдать {limit} +rep в сутки. "
    "Репутация показывается серой плашкой рядом с ником.\n\n"
    "<b>Команды:</b>\n"
    "/profile — карточка профиля (ник, репутация, сообщения, дата входа). "
    "Можно в ответ на сообщение или <code>/profile @username</code>\n"
    "/top — таблица топ-15 репутации картинкой\n"
    "/help — справка"
)

ADMIN_HELP_TEXT = (
    "\n\n<b>Для администратора бота:</b>\n"
    "/null — обнулить репутацию и запретить пользоваться ботом (в ответ или <code>/null @username</code>)\n"
    "/unnull — снять запрет\n"
    "/setrep N — установить репутацию (в ответ или <code>/setrep @username N</code>)\n"
    "/addrep N — прибавить/отнять репутацию (N может быть отрицательным)"
)


# ---------- вспомогательное ----------

def replied_user(message: Message) -> User | None:
    """Автор сообщения, на которое ответили (если это настоящий ответ)."""
    r = message.reply_to_message
    if r is None or r.from_user is None or r.sender_chat is not None:
        return None
    # в форумах «ответ» на корневое сообщение темы — это просто сообщение в теме
    if r.forum_topic_created is not None:
        return None
    if message.is_topic_message and r.message_id == message.message_thread_id:
        return None
    return r.from_user


def mention(m: Member) -> str:
    return f'<a href="tg://user?id={m.user_id}">{escape(m.display_name)}</a>'


async def ensure_member(db: Database, chat_id: int, user: User) -> Member:
    await db.touch(chat_id, user.id, user.username, user.full_name, count_message=False)
    return await db.get(chat_id, user.id)


async def resolve_target(
    message: Message, command: CommandObject, db: Database
) -> tuple[Member | None, list[str], str | None]:
    """Цель команды: ответом на сообщение, @username или числовой id.

    Возвращает (участник, оставшиеся аргументы, текст ошибки).
    """
    args = (command.args or "").split()
    reply_user = replied_user(message)
    if reply_user is not None and not (args and args[0].startswith("@")):
        if reply_user.is_bot:
            return None, args, "Это бот — у ботов нет профиля."
        return await ensure_member(db, message.chat.id, reply_user), args, None

    if args and args[0].startswith("@"):
        member = await db.find_by_username(message.chat.id, args[0])
        if member is None:
            return None, args[1:], f"Не знаю участника {escape(args[0])} — он ещё ничего не писал в этом чате."
        return member, args[1:], None

    if args and args[0].isdigit() and len(args[0]) >= 5:
        member = await db.get(message.chat.id, int(args[0]))
        if member is None:
            return None, args[1:], "Участник с таким id в этом чате не найден."
        return member, args[1:], None

    return None, args, None


async def deny_if_banned(message: Message, db: Database) -> bool:
    me = await db.get(message.chat.id, message.from_user.id)
    if me is not None and me.banned:
        await message.reply("⛔ Вам запрещено пользоваться ботом в этом чате.")
        return True
    return False


def fmt_date(ts: int, config: Config) -> str:
    return datetime.fromtimestamp(ts, config.tz).strftime("%d.%m.%Y")


# ---------- общие команды ----------

@router.message(CommandStart(), F.chat.type == "private")
async def cmd_start(message: Message, bot: Bot, config: Config) -> None:
    me = await bot.me()
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="➕ Добавить в группу", url=f"https://t.me/{me.username}?startgroup=true")
    ]])
    await message.answer(HELP_TEXT.format(limit=config.daily_rep_limit), reply_markup=kb)


@router.message(Command("help", "start"))
async def cmd_help(message: Message, config: Config) -> None:
    text = HELP_TEXT.format(limit=config.daily_rep_limit)
    if message.from_user and message.from_user.id in config.admin_ids:
        text += ADMIN_HELP_TEXT
    await message.reply(text)


@router.message(Command("profile"), group_only)
async def cmd_profile(message: Message, command: CommandObject, db: Database, config: Config) -> None:
    if await deny_if_banned(message, db):
        return
    target, _, error = await resolve_target(message, command, db)
    if error:
        await message.reply(error)
        return
    if target is None:
        target = await db.get(message.chat.id, message.from_user.id)
    if target is None:
        await message.reply("Профиль не найден. Ответьте на сообщение участника или укажите @username.")
        return

    place = await db.place(message.chat.id, target.rep)
    lines = [
        f"👤 <b>{escape(target.display_name)}</b>",
        f"🔗 @{escape(target.username)}" if target.username else f"🆔 <code>{target.user_id}</code>",
        "",
        f"⭐ Репутация: <b>{target.rep}</b>" + ("" if target.banned else f" (место #{place})"),
        f"💬 Сообщений: <b>{target.messages}</b>",
        f"📅 В группе с: <b>{fmt_date(target.joined_at, config)}</b>",
    ]
    if target.banned:
        lines.append("\n⛔ Доступ к боту заблокирован")
    await message.reply("\n".join(lines))


@router.message(Command("top"), group_only)
async def cmd_top(message: Message, bot: Bot, db: Database) -> None:
    if await deny_if_banned(message, db):
        return
    members = await db.top(message.chat.id, 15)
    avatars = await get_avatars(bot, [m.user_id for m in members])
    png = await asyncio.to_thread(render_top, message.chat.title or "", members, avatars)
    await message.reply_photo(BufferedInputFile(png, filename="top.png"))


@router.message(Command("profile", "top"))
async def cmd_group_only(message: Message) -> None:
    await message.reply("Эта команда работает в группе. Добавьте меня в группу через /start.")


# ---------- +rep ----------

def is_plus_rep(text: str | None) -> bool:
    if not text:
        return False
    t = text.strip().lower()
    for word in ("+rep", "+реп"):
        if t.startswith(word) and (len(t) == len(word) or not t[len(word)].isalnum()):
            return True
    return False


@router.message(group_only, F.text.func(is_plus_rep))
async def plus_rep(message: Message, bot: Bot, db: Database, config: Config) -> None:
    giver = message.from_user
    if giver is None or giver.is_bot or message.sender_chat is not None:
        return
    target_user = replied_user(message)
    if target_user is None:
        await message.reply("Чтобы выдать +rep, ответьте на сообщение участника.")
        return
    if target_user.is_bot:
        await message.reply("Ботам репутацию не выдают 🙂")
        return
    if target_user.id == giver.id:
        await message.reply("Себе репутацию поднять нельзя.")
        return
    if await deny_if_banned(message, db):
        return

    chat_id = message.chat.id
    async with rep_lock:
        target = await ensure_member(db, chat_id, target_user)
        if target.banned:
            await message.reply("Этому участнику запрещено пользоваться ботом — репутацию ему выдать нельзя.")
            return

        now = int(time.time())
        given, oldest = await db.given_since(chat_id, giver.id, now - 86400)
        if given >= config.daily_rep_limit:
            wait = oldest + 86400 - now
            await message.reply(
                f"Лимит исчерпан: можно выдать {config.daily_rep_limit} +rep в сутки.\n"
                f"Следующий будет доступен через {wait // 3600} ч {wait % 3600 // 60} мин."
            )
            return

        new_rep = await db.add_rep(chat_id, target.user_id, 1)
        await db.log_rep(chat_id, giver.id, target.user_id)

    giver_member = await db.get(chat_id, giver.id)
    await apply_tag(bot, chat_id, target.user_id, make_tag(config.tag_format, new_rep))
    left = config.daily_rep_limit - given - 1
    await message.reply(
        f"👍 +1 к репутации {mention(target)} от {mention(giver_member)}\n"
        f"⭐ Теперь: <b>{new_rep}</b>\n"
        f"Осталось +rep на сутки: {left}/{config.daily_rep_limit}"
    )


# ---------- вступление в группу ----------

@router.chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def on_join(event: ChatMemberUpdated, db: Database) -> None:
    user = event.new_chat_member.user
    if user.is_bot:
        return
    ts = int(event.date.timestamp())
    await db.touch(event.chat.id, user.id, user.username, user.full_name, count_message=False, joined_at=ts)
    await db.set_joined(event.chat.id, user.id, ts)


@router.message(group_only, F.new_chat_members)
async def on_new_members(message: Message, db: Database) -> None:
    ts = int(message.date.timestamp())
    for user in message.new_chat_members:
        if user.is_bot:
            continue
        await db.touch(message.chat.id, user.id, user.username, user.full_name, count_message=False, joined_at=ts)
        await db.set_joined(message.chat.id, user.id, ts)


# ---------- команды администратора бота ----------

async def admin_target(
    message: Message, command: CommandObject, db: Database, config: Config
) -> tuple[Member | None, list[str]]:
    if message.from_user is None or message.from_user.id not in config.admin_ids:
        await message.reply("Эта команда доступна только администратору бота.")
        return None, []
    target, args, error = await resolve_target(message, command, db)
    if error:
        await message.reply(error)
        return None, []
    if target is None:
        await message.reply(
            f"Укажите участника: ответьте на его сообщение или напишите <code>/{command.command} @username</code>."
        )
        return None, []
    return target, args


@router.message(Command("null"), group_only)
async def cmd_null(message: Message, command: CommandObject, bot: Bot, db: Database, config: Config) -> None:
    target, _ = await admin_target(message, command, db, config)
    if target is None:
        return
    await db.set_rep(message.chat.id, target.user_id, 0)
    await db.set_banned(message.chat.id, target.user_id, True)
    await apply_tag(bot, message.chat.id, target.user_id, "")
    await message.reply(f"🚫 Репутация {mention(target)} обнулена, доступ к боту в этом чате закрыт.")


@router.message(Command("unnull"), group_only)
async def cmd_unnull(message: Message, command: CommandObject, bot: Bot, db: Database, config: Config) -> None:
    target, _ = await admin_target(message, command, db, config)
    if target is None:
        return
    await db.set_banned(message.chat.id, target.user_id, False)
    await apply_tag(bot, message.chat.id, target.user_id, make_tag(config.tag_format, target.rep))
    await message.reply(f"✅ {mention(target)} снова может пользоваться ботом.")


async def _change_rep(
    message: Message, command: CommandObject, bot: Bot, db: Database, config: Config, absolute: bool
) -> None:
    target, args = await admin_target(message, command, db, config)
    if target is None:
        return
    try:
        value = int(args[0])
    except (IndexError, ValueError):
        await message.reply(f"Укажите число: <code>/{command.command} @username 10</code> или ответом <code>/{command.command} 10</code>.")
        return
    async with rep_lock:
        if absolute:
            await db.set_rep(message.chat.id, target.user_id, value)
            new_rep = value
        else:
            new_rep = await db.add_rep(message.chat.id, target.user_id, value)
    if not target.banned:
        await apply_tag(bot, message.chat.id, target.user_id, make_tag(config.tag_format, new_rep))
    await message.reply(f"✏️ Репутация {mention(target)}: <b>{new_rep}</b>")


@router.message(Command("setrep"), group_only)
async def cmd_setrep(message: Message, command: CommandObject, bot: Bot, db: Database, config: Config) -> None:
    await _change_rep(message, command, bot, db, config, absolute=True)


@router.message(Command("addrep"), group_only)
async def cmd_addrep(message: Message, command: CommandObject, bot: Bot, db: Database, config: Config) -> None:
    await _change_rep(message, command, bot, db, config, absolute=False)
