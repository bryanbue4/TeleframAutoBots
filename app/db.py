from datetime import date
from pathlib import Path

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

CREATE TABLE IF NOT EXISTS channel_routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_chat TEXT NOT NULL,
    destination_chat TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source_chat, destination_chat)
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL,
    direction TEXT NOT NULL,
    text TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS intake_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS team_members (
    telegram_id INTEGER PRIMARY KEY,
    name TEXT,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS relays (
    customer_id INTEGER PRIMARY KEY,
    team_member_id INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_handler_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trusted_devices (
    token TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expiry TEXT NOT NULL
);
"""


async def _ensure_column(db, table: str, column: str, decl: str) -> None:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in await cursor.fetchall()}
    if column not in existing:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


async def init_db(db_path: str) -> None:
    # Ensure the parent directory exists (e.g. /data on Railway) so SQLite can
    # create the file instead of crashing with "unable to open database file".
    parent = Path(db_path).parent
    if parent and str(parent) not in (".", ""):
        parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        # Migrate older databases that predate newer columns.
        await _ensure_column(db, "relays", "last_handler_at", "TEXT")
        await db.commit()


async def add_route(db_path: str, source_chat: str, destination_chat: str) -> bool:
    """Add a source -> destination copy route. Returns False if it already exists."""
    async with aiosqlite.connect(db_path) as db:
        try:
            await db.execute(
                "INSERT INTO channel_routes (source_chat, destination_chat) VALUES (?, ?)",
                (source_chat, destination_chat),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def remove_route(db_path: str, route_id: int) -> bool:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("DELETE FROM channel_routes WHERE id = ?", (route_id,))
        await db.commit()
        return cursor.rowcount > 0


async def list_routes(db_path: str) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, source_chat, destination_chat, active FROM channel_routes ORDER BY id"
        )
        return [dict(row) for row in await cursor.fetchall()]


async def get_active_routes(db_path: str) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT source_chat, destination_chat FROM channel_routes WHERE active = 1"
        )
        return [dict(row) for row in await cursor.fetchall()]


async def seed_routes_if_empty(db_path: str, sources: list[str], destination: str) -> None:
    """One-time migration: if there are no routes yet but the old env-based
    SOURCE_CHATS/DESTINATION_CHAT_ID are set, create a route for each source so
    existing behaviour continues without manual setup.
    """
    if not sources or not destination:
        return
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM channel_routes")
        (count,) = await cursor.fetchone()
        if count:
            return
        for source in sources:
            await db.execute(
                "INSERT OR IGNORE INTO channel_routes (source_chat, destination_chat) VALUES (?, ?)",
                (source, str(destination)),
            )
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


async def save_chat_message(db_path: str, telegram_user_id: int, direction: str, text: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO chat_messages (telegram_user_id, direction, text) VALUES (?, ?, ?)",
            (telegram_user_id, direction, text),
        )
        await db.commit()


async def count_incoming(db_path: str, telegram_user_id: int) -> int:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE telegram_user_id = ? AND direction = 'in'",
            (telegram_user_id,),
        )
        (count,) = await cursor.fetchone()
        return count


async def get_chat_history(db_path: str, telegram_user_id: int, limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT direction, text, created_at FROM chat_messages
            WHERE telegram_user_id = ? ORDER BY id DESC LIMIT ?
            """,
            (telegram_user_id, limit),
        )
        rows = [dict(r) for r in await cursor.fetchall()]
        return list(reversed(rows))


async def add_question(db_path: str, question: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("INSERT INTO intake_questions (question) VALUES (?)", (question,))
        await db.commit()


async def list_questions(db_path: str) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id, question, active FROM intake_questions ORDER BY id")
        return [dict(r) for r in await cursor.fetchall()]


async def remove_question(db_path: str, question_id: int) -> bool:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("DELETE FROM intake_questions WHERE id = ?", (question_id,))
        await db.commit()
        return cursor.rowcount > 0


async def get_active_questions(db_path: str) -> list[str]:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT question FROM intake_questions WHERE active = 1 ORDER BY id")
        return [row[0] for row in await cursor.fetchall()]


async def add_team_member(db_path: str, telegram_id: int, name: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO team_members (telegram_id, name, active) VALUES (?, ?, 1)",
            (telegram_id, name),
        )
        await db.commit()


async def list_team_members(db_path: str) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT telegram_id, name, active FROM team_members ORDER BY name")
        return [dict(r) for r in await cursor.fetchall()]


async def remove_team_member(db_path: str, telegram_id: int) -> bool:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("DELETE FROM team_members WHERE telegram_id = ?", (telegram_id,))
        await db.commit()
        return cursor.rowcount > 0


async def start_relay(db_path: str, customer_id: int, team_member_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO relays (customer_id, team_member_id, active) VALUES (?, ?, 1)",
            (customer_id, team_member_id),
        )
        await db.commit()


async def end_relay(db_path: str, customer_id: int) -> bool:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("DELETE FROM relays WHERE customer_id = ?", (customer_id,))
        await db.commit()
        return cursor.rowcount > 0


async def team_for_customer(db_path: str, customer_id: int) -> int | None:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT team_member_id FROM relays WHERE customer_id = ? AND active = 1", (customer_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def list_active_relays(db_path: str) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT customer_id, team_member_id FROM relays WHERE active = 1 ORDER BY created_at"
        )
        return [dict(r) for r in await cursor.fetchall()]


async def customer_for_team(db_path: str, team_member_id: int) -> int | None:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT customer_id FROM relays WHERE team_member_id = ? AND active = 1 LIMIT 1",
            (team_member_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def get_relay(db_path: str, customer_id: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT customer_id, team_member_id, last_handler_at FROM relays "
            "WHERE customer_id = ? AND active = 1",
            (customer_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def touch_relay(db_path: str, customer_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE relays SET last_handler_at = datetime('now') WHERE customer_id = ?",
            (customer_id,),
        )
        await db.commit()


async def relay_seconds_since_handler(db_path: str, customer_id: int) -> float | None:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT strftime('%s','now') - strftime('%s', last_handler_at) FROM relays "
            "WHERE customer_id = ? AND active = 1",
            (customer_id,),
        )
        row = await cursor.fetchone()
        return float(row[0]) if row and row[0] is not None else None


async def list_flagged_members(db_path: str) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT telegram_user_id, name, suspicious_reasons FROM customers
            WHERE is_suspicious_flag = 1 AND status != 'removed'
            ORDER BY last_message_at DESC
            """
        )
        return [dict(r) for r in await cursor.fetchall()]


async def list_recent_customers(db_path: str, limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT c.telegram_user_id, c.name, c.message_count,
                   (SELECT team_member_id FROM relays r WHERE r.customer_id = c.telegram_user_id
                    AND r.active = 1) AS handler
            FROM customers c
            WHERE c.last_message_at IS NOT NULL
            ORDER BY c.last_message_at DESC LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in await cursor.fetchall()]


async def add_trusted_device(db_path: str, token: str, days: int = 30) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO trusted_devices (token, expiry) VALUES (?, datetime('now', ?))",
            (token, f"+{days} days"),
        )
        await db.commit()


async def is_trusted_device(db_path: str, token: str) -> bool:
    if not token:
        return False
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT 1 FROM trusted_devices WHERE token = ? AND expiry > datetime('now')",
            (token,),
        )
        return await cursor.fetchone() is not None


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
