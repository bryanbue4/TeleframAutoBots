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
    telethon_session_string: str
    source_chats: list[str]
    destination_chat_id: int
    openrouter_api_key: str
    openrouter_model: str
    timezone: str
    morning_time: str
    night_time: str
    db_path: str
    dashboard_password: str
    port: int


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value or not value.strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    # Strip surrounding whitespace/newlines - a trailing newline from a paste
    # in the host's dashboard is a common cause of 401s (the token is sent verbatim).
    return value.strip()


def _optional(name: str, default: str) -> str:
    value = os.environ.get(name, default)
    return value.strip() if value else default


def load_settings() -> Settings:
    return Settings(
        customer_bot_token=_require("CUSTOMER_BOT_TOKEN"),
        admin_bot_token=_require("ADMIN_BOT_TOKEN"),
        admin_telegram_id=int(_require("ADMIN_TELEGRAM_ID")),
        telethon_api_id=int(_require("TELETHON_API_ID")),
        telethon_api_hash=_require("TELETHON_API_HASH"),
        telethon_session_name=_optional("TELETHON_SESSION_NAME", "content_listener"),
        telethon_session_string=_optional("TELETHON_SESSION_STRING", ""),
        source_chats=[c.strip() for c in os.environ.get("SOURCE_CHATS", "").split(",") if c.strip()],
        destination_chat_id=int(_require("DESTINATION_CHAT_ID")),
        openrouter_api_key=_require("OPENROUTER_API_KEY"),
        openrouter_model=_optional("OPENROUTER_MODEL", "deepseek/deepseek-chat"),
        timezone=_optional("TIMEZONE", "UTC"),
        morning_time=_optional("MORNING_TIME", "08:00"),
        night_time=_optional("NIGHT_TIME", "21:00"),
        db_path=_optional("DB_PATH", "data/app.db"),
        dashboard_password=os.environ.get("DASHBOARD_PASSWORD", ""),
        port=int(os.environ.get("PORT", "8080")),
    )
