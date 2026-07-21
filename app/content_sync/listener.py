import asyncio
import logging

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from app.config import Settings
from app.db import get_active_routes, record_content_if_new

logger = logging.getLogger(__name__)

FORWARD_DELAY_SECONDS = 5
ROUTE_REFRESH_SECONDS = 30


def _to_entity(destination: str):
    """Destinations may be an @username or a numeric channel id (as text)."""
    destination = destination.strip()
    if destination.startswith("@"):
        return destination
    try:
        return int(destination)
    except ValueError:
        return destination


def _source_matches(source: str, event) -> bool:
    source = source.strip()
    if source.startswith("@"):
        username = getattr(event.chat, "username", None)
        return bool(username) and username.lower() == source[1:].lower()
    try:
        return int(source) == event.chat_id
    except (ValueError, TypeError):
        return str(event.chat_id) == source


class ContentListener:
    """Copies new posts from source channels the user account has joined into the
    owner's own destination channels, based on routes stored in the database.

    Routes (source -> destination) are editable at runtime via the admin bot and
    reloaded periodically, so no redeploy is needed to add channels. Posts are
    copied (not forwarded), so they carry no "Forwarded from" header.

    Uses a Telethon user session (not the Bot API) because the Bot API can only
    read chats where the bot itself is an admin/member - not arbitrary channels
    the owner has personally joined.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        session = (
            StringSession(settings.telethon_session_string)
            if settings.telethon_session_string
            else settings.telethon_session_name
        )
        self.client = TelegramClient(
            session,
            settings.telethon_api_id,
            settings.telethon_api_hash,
        )
        self._queue: asyncio.Queue = asyncio.Queue()
        self._routes: list[dict] = []

    async def _refresh_routes(self) -> None:
        self._routes = await get_active_routes(self.settings.db_path)

    async def _route_refresher(self) -> None:
        while True:
            await asyncio.sleep(ROUTE_REFRESH_SECONDS)
            try:
                await self._refresh_routes()
            except Exception:
                logger.exception("Failed to refresh routes")

    async def start(self) -> None:
        await self.client.start()
        await self._refresh_routes()
        # Listen broadly and match against routes in the handler, so newly added
        # source channels are picked up without re-registering Telethon filters.
        self.client.add_event_handler(self._on_new_message, events.NewMessage())
        asyncio.create_task(self._copy_worker())
        asyncio.create_task(self._route_refresher())
        logger.info("Content listener started with %d route(s)", len(self._routes))

    async def _on_new_message(self, event) -> None:
        destinations = [
            r["destination_chat"] for r in self._routes if _source_matches(r["source_chat"], event)
        ]
        if not destinations:
            return
        is_new = await record_content_if_new(self.settings.db_path, event.chat_id, event.id)
        if is_new:
            await self._queue.put((event.message, destinations))

    async def _copy_worker(self) -> None:
        # Single consumer serializes posts so order is preserved and they are
        # spaced out instead of arriving in a spammy burst. Content is copied
        # (re-sent), not forwarded, so there is no "Forwarded from" header.
        while True:
            message, destinations = await self._queue.get()
            for destination in destinations:
                try:
                    entity = _to_entity(destination)
                    if message.media:
                        await self.client.send_file(
                            entity,
                            message.media,
                            caption=message.message or "",
                            formatting_entities=message.entities,
                        )
                    elif message.message:
                        await self.client.send_message(
                            entity,
                            message.message,
                            formatting_entities=message.entities,
                        )
                except Exception:
                    logger.exception("Failed to copy message %s to %s", message.id, destination)
                await asyncio.sleep(FORWARD_DELAY_SECONDS)

    async def run_forever(self) -> None:
        await self.client.run_until_disconnected()
