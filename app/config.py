import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    customer_bot_token: str
    admin_bot_token: str
    admin_telegram_id: int
    telethon_api_id: int
    telethon_api_hash: str
    telethon_session_name: str
    source_chats: list[str]
    destination_chat_id: int
    openrouter_api_key: str
    openrouter_model: str
    timezone: str
    morning_time: str
    night_time: str
    db_path: str


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_settings() -> Settings:
    return Settings(
        customer_bot_token=_require("CUSTOMER_BOT_TOKEN"),
        admin_bot_token=_require("ADMIN_BOT_TOKEN"),
        admin_telegram_id=int(_require("ADMIN_TELEGRAM_ID")),
        telethon_api_id=int(_require("TELETHON_API_ID")),
        telethon_api_hash=_require("TELETHON_API_HASH"),
        telethon_session_name=os.environ.get("TELETHON_SESSION_NAME", "content_listener"),
        source_chats=[c.strip() for c in os.environ.get("SOURCE_CHATS", "").split(",") if c.strip()],
        destination_chat_id=int(_require("DESTINATION_CHAT_ID")),
        openrouter_api_key=_require("OPENROUTER_API_KEY"),
        openrouter_model=os.environ.get("OPENROUTER_MODEL", "anthropic/claude-3.5-haiku"),
        timezone=os.environ.get("TIMEZONE", "UTC"),
        morning_time=os.environ.get("MORNING_TIME", "08:00"),
        night_time=os.environ.get("NIGHT_TIME", "21:00"),
        db_path=os.environ.get("DB_PATH", "data/app.db"),
    )
