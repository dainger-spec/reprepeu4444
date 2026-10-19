"""Хранилище данных.

По умолчанию — SQLite-файл (DB_PATH), который переживает перезапуск бота,
если лежит на постоянном диске/volume. Если задан DATABASE_URL
(postgres://...), используется PostgreSQL — удобно для хостингов
с «одноразовой» файловой системой (Railway, Render, Heroku и т.п.).
"""

import re
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        chat_id   BIGINT NOT NULL,
        user_id   BIGINT NOT NULL,
        username  TEXT,
        full_name TEXT NOT NULL DEFAULT '',
        rep       BIGINT NOT NULL DEFAULT 0,
        messages  BIGINT NOT NULL DEFAULT 0,
        joined_at BIGINT NOT NULL,
        banned    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (chat_id, user_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS users_username ON users (chat_id, username)",
    "CREATE INDEX IF NOT EXISTS users_rep ON users (chat_id, rep)",
    """
    CREATE TABLE IF NOT EXISTS rep_log (
        chat_id BIGINT NOT NULL,
        from_id BIGINT NOT NULL,
        to_id   BIGINT NOT NULL,
        ts      BIGINT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS rep_log_from ON rep_log (chat_id, from_id, ts)",
    # перенесённая репутация: начисляется, когда человек с этим ником появится в чате
    """
    CREATE TABLE IF NOT EXISTS rep_import (
        chat_id    BIGINT NOT NULL,
        username   TEXT NOT NULL,
        rep        BIGINT NOT NULL,
        applied_to BIGINT,
        PRIMARY KEY (chat_id, username)
    )
    """,
    # +rep по @username тем, кого бот ещё не видел: начислится при первом появлении
    """
    CREATE TABLE IF NOT EXISTS pending_rep (
        chat_id  BIGINT NOT NULL,
        username TEXT NOT NULL,
        rep      BIGINT NOT NULL,
        PRIMARY KEY (chat_id, username)
    )
    """,
    # группы и решение администратора бота: allowed / denied / pending
    """
    CREATE TABLE IF NOT EXISTS chats (
        chat_id    BIGINT PRIMARY KEY,
        title      TEXT NOT NULL DEFAULT '',
        status     TEXT NOT NULL,
        updated_at BIGINT NOT NULL
    )
    """,
]


@dataclass
class Member:
    chat_id: int
    user_id: int
    username: str | None
    full_name: str
    rep: int
    messages: int
    joined_at: int
    banned: bool

    @property
    def display_name(self) -> str:
        return self.full_name or (f"@{self.username}" if self.username else str(self.user_id))


def _member(row) -> Member | None:
    if row is None:
        return None
    return Member(
        chat_id=row[0],
        user_id=row[1],
        username=row[2],
        full_name=row[3],
        rep=row[4],
        messages=row[5],
        joined_at=row[6],
        banned=bool(row[7]),
    )


MEMBER_COLS = "chat_id, user_id, username, full_name, rep, messages, joined_at, banned"


class Database:
    def __init__(self, database_url: str | None, db_path: Path):
        self._url = database_url
        self._path = db_path
        self._pg = None
        self._lite = None

    # ---------- низкоуровневые операции ----------

    async def connect(self) -> None:
        if self._url:
            import asyncpg

            self._pg = await asyncpg.create_pool(self._url, min_size=1, max_size=5)
        else:
            import aiosqlite

            self._lite = await aiosqlite.connect(self._path)
            await self._lite.execute("PRAGMA journal_mode=WAL")
            await self._lite.execute("PRAGMA synchronous=NORMAL")
        for stmt in SCHEMA:
            await self.execute(stmt)

    async def close(self) -> None:
        if self._pg:
            await self._pg.close()
        if self._lite:
            await self._lite.close()

    @staticmethod
    def _to_pg(sql: str) -> str:
        counter = iter(range(1, 1000))
        return re.sub(r"\?", lambda _: f"${next(counter)}", sql)

    async def execute(self, sql: str, *args) -> None:
        if self._pg:
            await self._pg.execute(self._to_pg(sql), *args)
        else:
            await self._lite.execute(sql, args)
            await self._lite.commit()

    async def fetchone(self, sql: str, *args):
        if self._pg:
            return await self._pg.fetchrow(self._to_pg(sql), *args)
        async with self._lite.execute(sql, args) as cur:
            return await cur.fetchone()

    async def fetchall(self, sql: str, *args) -> list:
        if self._pg:
            return await self._pg.fetch(self._to_pg(sql), *args)
        async with self._lite.execute(sql, args) as cur:
            return list(await cur.fetchall())

    # ---------- участники ----------

    async def touch(
        self,
        chat_id: int,
        user_id: int,
        username: str | None,
        full_name: str,
        count_message: bool,
        joined_at: int | None = None,
    ) -> None:
        """Создаёт/обновляет профиль и при необходимости увеличивает счётчик сообщений."""
        now = int(time.time())
        inc = 1 if count_message else 0
        await self.execute(
            f"""
            INSERT INTO users ({MEMBER_COLS})
            VALUES (?, ?, ?, ?, 0, ?, ?, 0)
            ON CONFLICT (chat_id, user_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name,
                messages = users.messages + ?
            """,
            chat_id, user_id, username, full_name, inc, joined_at or now, inc,
        )

    async def set_joined(self, chat_id: int, user_id: int, joined_at: int) -> None:
        await self.execute(
            "UPDATE users SET joined_at = ? WHERE chat_id = ? AND user_id = ?",
            joined_at, chat_id, user_id,
        )

    async def get(self, chat_id: int, user_id: int) -> Member | None:
        row = await self.fetchone(
            f"SELECT {MEMBER_COLS} FROM users WHERE chat_id = ? AND user_id = ?",
            chat_id, user_id,
        )
        return _member(row)

    async def find_by_username(self, chat_id: int, username: str) -> Member | None:
        row = await self.fetchone(
            f"SELECT {MEMBER_COLS} FROM users WHERE chat_id = ? AND LOWER(username) = LOWER(?)",
            chat_id, username.lstrip("@"),
        )
        return _member(row)

    async def top(self, chat_id: int, limit: int = 15) -> list[Member]:
        rows = await self.fetchall(
            f"""
            SELECT {MEMBER_COLS} FROM users
            WHERE chat_id = ? AND banned = 0 AND rep > 0
            ORDER BY rep DESC, messages DESC
            LIMIT ?
            """,
            chat_id, limit,
        )
        return [_member(r) for r in rows]

    async def place(self, chat_id: int, rep: int) -> int:
        row = await self.fetchone(
            "SELECT COUNT(*) FROM users WHERE chat_id = ? AND banned = 0 AND rep > ?",
            chat_id, rep,
        )
        return row[0] + 1

    async def member_count(self, chat_id: int) -> int:
        row = await self.fetchone("SELECT COUNT(*) FROM users WHERE chat_id = ? AND banned = 0", chat_id)
        return row[0]

    async def add_rep(self, chat_id: int, user_id: int, delta: int) -> int:
        await self.execute(
            "UPDATE users SET rep = rep + ? WHERE chat_id = ? AND user_id = ?",
            delta, chat_id, user_id,
        )
        return (await self.get(chat_id, user_id)).rep

    async def set_rep(self, chat_id: int, user_id: int, rep: int) -> None:
        await self.execute(
            "UPDATE users SET rep = ? WHERE chat_id = ? AND user_id = ?",
            rep, chat_id, user_id,
        )

    async def set_banned(self, chat_id: int, user_id: int, banned: bool) -> None:
        await self.execute(
            "UPDATE users SET banned = ? WHERE chat_id = ? AND user_id = ?",
            int(banned), chat_id, user_id,
        )

    # ---------- лимит +rep ----------

    async def given_since(self, chat_id: int, from_id: int, since: int) -> tuple[int, int | None]:
        """Сколько +rep выдано с момента since и время самой ранней из этих выдач."""
        row = await self.fetchone(
            "SELECT COUNT(*), MIN(ts) FROM rep_log WHERE chat_id = ? AND from_id = ? AND ts > ?",
            chat_id, from_id, since,
        )
        return row[0], row[1]

    async def log_rep(self, chat_id: int, from_id: int, to_id: int) -> None:
        now = int(time.time())
        await self.execute(
            "INSERT INTO rep_log (chat_id, from_id, to_id, ts) VALUES (?, ?, ?, ?)",
            chat_id, from_id, to_id, now,
        )
        # старые записи для лимита больше не нужны
        await self.execute("DELETE FROM rep_log WHERE ts < ?", now - 7 * 86400)

    # ---------- перенос репутации ----------

    async def add_import(self, chat_id: int, username: str, rep: int) -> bool:
        """Добавляет запись переноса. False — такая уже была (повторный импорт)."""
        if await self.fetchone(
            "SELECT 1 FROM rep_import WHERE chat_id = ? AND username = ?", chat_id, username
        ):
            return False
        await self.execute(
            "INSERT INTO rep_import (chat_id, username, rep) VALUES (?, ?, ?)",
            chat_id, username, rep,
        )
        return True

    async def pending_imports(self) -> list[tuple[int, str, int]]:
        rows = await self.fetchall(
            "SELECT chat_id, username, rep FROM rep_import WHERE applied_to IS NULL"
        )
        return [(r[0], r[1], r[2]) for r in rows]

    async def mark_import_applied(self, chat_id: int, username: str, user_id: int) -> None:
        await self.execute(
            "UPDATE rep_import SET applied_to = ? WHERE chat_id = ? AND username = ?",
            user_id, chat_id, username,
        )

    async def users_with_username(self, chat_id: int) -> list[tuple[int, str]]:
        rows = await self.fetchall(
            "SELECT user_id, username FROM users WHERE chat_id = ? AND username IS NOT NULL",
            chat_id,
        )
        return [(r[0], r[1]) for r in rows]

    # ---------- доступ групп ----------

    async def set_chat_status(self, chat_id: int, title: str, status: str) -> None:
        await self.execute(
            """
            INSERT INTO chats (chat_id, title, status, updated_at) VALUES (?, ?, ?, ?)
            ON CONFLICT (chat_id) DO UPDATE SET
                title = excluded.title, status = excluded.status, updated_at = excluded.updated_at
            """,
            chat_id, title, status, int(time.time()),
        )

    async def all_chats(self) -> list[tuple[int, str, str]]:
        rows = await self.fetchall("SELECT chat_id, title, status FROM chats ORDER BY updated_at DESC")
        return [(r[0], r[1], r[2]) for r in rows]

    async def migrate_chat(self, old_id: int, new_id: int) -> None:
        """Группа стала супергруппой и получила новый ID — переносим все данные."""
        if await self.fetchone("SELECT 1 FROM users WHERE chat_id = ? LIMIT 1", new_id) is None:
            await self.execute("UPDATE users SET chat_id = ? WHERE chat_id = ?", new_id, old_id)
        await self.execute("UPDATE rep_log SET chat_id = ? WHERE chat_id = ?", new_id, old_id)
        await self.execute("UPDATE rep_import SET chat_id = ? WHERE chat_id = ?", new_id, old_id)

    # ---------- отложенная репутация по @username ----------

    async def add_pending_rep(self, chat_id: int, username: str, delta: int) -> None:
        await self.execute(
            """
            INSERT INTO pending_rep (chat_id, username, rep) VALUES (?, ?, ?)
            ON CONFLICT (chat_id, username) DO UPDATE SET rep = pending_rep.rep + excluded.rep
            """,
            chat_id, username, delta,
        )

    async def all_pending_rep(self) -> list[tuple[int, str, int]]:
        rows = await self.fetchall("SELECT chat_id, username, rep FROM pending_rep")
        return [(r[0], r[1], r[2]) for r in rows]

    async def delete_pending_rep(self, chat_id: int, username: str) -> None:
        await self.execute("DELETE FROM pending_rep WHERE chat_id = ? AND username = ?", chat_id, username)
