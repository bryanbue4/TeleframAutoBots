from datetime import date

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    telegram_user_id INTEGER PRIMARY KEY,
    name TEXT,
    city TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    joined_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_message_at TEXT,
    message_count INTEGER NOT NULL DEFAULT 0,
    is_duplicate_flag INTEGER NOT NULL DEFAULT 0,
    is_suspicious_flag INTEGER NOT NULL DEFAULT 0,
    suspicious_reasons TEXT,
    removed_at TEXT,
    removed_reason TEXT
);

CREATE TABLE IF NOT EXISTS content_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_chat_id TEXT NOT NULL,
    source_message_id INTEGER NOT NULL,
    posted_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source_chat_id, source_message_id)
);

CREATE TABLE IF NOT EXISTS daily_stats (
    stat_date TEXT PRIMARY KEY,
    new_members INTEGER NOT NULL DEFAULT 0,
    pending_members INTEGER NOT NULL DEFAULT 0,
    suspicious_members INTEGER NOT NULL DEFAULT 0,
    new_user_messages INTEGER NOT NULL DEFAULT 0,
    old_user_messages INTEGER NOT NULL DEFAULT 0,
    active_chats INTEGER NOT NULL DEFAULT 0
);
"""


async def init_db(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def record_content_if_new(db_path: str, source_chat_id: str, source_message_id: int) -> bool:
    """Returns True if this is new content (not previously posted)."""
    async with aiosqlite.connect(db_path) as db:
        try:
            await db.execute(
                "INSERT INTO content_log (source_chat_id, source_message_id) VALUES (?, ?)",
                (str(source_chat_id), source_message_id),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def upsert_join_request(db_path: str, telegram_user_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO customers (telegram_user_id, status)
            VALUES (?, 'pending')
            ON CONFLICT(telegram_user_id) DO NOTHING
            """,
            (telegram_user_id,),
        )
        await db.commit()


async def set_customer_status(db_path: str, telegram_user_id: int, status: str, reason: str | None = None) -> None:
    async with aiosqlite.connect(db_path) as db:
        if status == "removed":
            await db.execute(
                """
                UPDATE customers SET status = ?, removed_at = datetime('now'), removed_reason = ?
                WHERE telegram_user_id = ?
                """,
                (status, reason, telegram_user_id),
            )
        else:
            await db.execute(
                "UPDATE customers SET status = ? WHERE telegram_user_id = ?",
                (status, telegram_user_id),
            )
        await db.commit()


async def find_duplicate(db_path: str, name: str, city: str, exclude_user_id: int) -> int | None:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            """
            SELECT telegram_user_id FROM customers
            WHERE lower(name) = lower(?) AND lower(city) = lower(?)
              AND telegram_user_id != ? AND status != 'removed'
            LIMIT 1
            """,
            (name, city, exclude_user_id),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def set_customer_details(db_path: str, telegram_user_id: int, name: str, city: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE customers SET name = ?, city = ? WHERE telegram_user_id = ?",
            (name, city, telegram_user_id),
        )
        await db.commit()


async def flag_duplicate(db_path: str, telegram_user_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE customers SET is_duplicate_flag = 1 WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )
        await db.commit()


async def flag_suspicious(db_path: str, telegram_user_id: int, reason: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            UPDATE customers SET is_suspicious_flag = 1,
                suspicious_reasons = trim(coalesce(suspicious_reasons, '') || ? || '; ')
            WHERE telegram_user_id = ?
            """,
            (reason, telegram_user_id),
        )
        await db.commit()


async def record_message(db_path: str, telegram_user_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            UPDATE customers SET message_count = message_count + 1, last_message_at = datetime('now')
            WHERE telegram_user_id = ?
            """,
            (telegram_user_id,),
        )
        await db.commit()


async def all_active_customers(db_path: str) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM customers WHERE status = 'approved'"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def build_daily_report(db_path: str) -> dict:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        async def count(query: str, *params) -> int:
            cursor = await db.execute(query, params)
            row = await cursor.fetchone()
            return row[0] if row else 0

        new_members = await count(
            "SELECT COUNT(*) FROM customers WHERE date(joined_at) = date('now')"
        )
        pending_members = await count(
            "SELECT COUNT(*) FROM customers WHERE status = 'pending'"
        )
        suspicious_members = await count(
            "SELECT COUNT(*) FROM customers WHERE is_suspicious_flag = 1"
        )
        new_user_messages = await count(
            """
            SELECT COUNT(*) FROM customers
            WHERE date(joined_at) = date('now') AND last_message_at IS NOT NULL
              AND date(last_message_at) = date('now')
            """
        )
        old_user_messages = await count(
            """
            SELECT COUNT(*) FROM customers
            WHERE date(joined_at) != date('now') AND last_message_at IS NOT NULL
              AND date(last_message_at) = date('now')
            """
        )
        active_chats = await count(
            "SELECT COUNT(*) FROM customers WHERE date(last_message_at) = date('now')"
        )

        return {
            "date": date.today().isoformat(),
            "new_members": new_members,
            "pending_members": pending_members,
            "suspicious_members": suspicious_members,
            "new_user_messages": new_user_messages,
            "old_user_messages": old_user_messages,
            "active_chats": active_chats,
        }
