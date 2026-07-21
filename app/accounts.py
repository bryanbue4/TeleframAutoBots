import dataclasses
import os

from app.config import Settings


def account_db_path(base_db_path: str, account_id: int) -> str:
    """Store each account's database next to the primary one, so a single
    persistent volume (mounted where the primary DB lives) covers them all.
    """
    base_dir = os.path.dirname(base_db_path) or "."
    return os.path.join(base_dir, f"acct_{account_id}.db")


def build_account_settings(base: Settings, row: dict) -> Settings:
    """Build a per-account Settings from a stored account row, reusing the base
    (shared) settings for AI, timezone, and schedule, but with this account's
    own tokens, session, channels, and an isolated database file.
    """
    return dataclasses.replace(
        base,
        customer_bot_token=row["customer_bot_token"],
        admin_bot_token=row["admin_bot_token"],
        admin_telegram_id=int(row["admin_telegram_id"]),
        telethon_api_id=int(row["telethon_api_id"]) if row.get("telethon_api_id") else base.telethon_api_id,
        telethon_api_hash=row.get("telethon_api_hash") or base.telethon_api_hash,
        telethon_session_string=row.get("telethon_session_string") or "",
        telethon_session_name=f"acct_{row['id']}",
        source_chats=[c.strip() for c in (row.get("source_chats") or "").split(",") if c.strip()],
        destination_chat_id=int(row["destination_chat_id"]) if row.get("destination_chat_id") else 0,
        db_path=account_db_path(base.db_path, row["id"]),
    )
