import asyncio
import logging

from telethon import TelegramClient, events

from app.config import Settings
from app.db import record_content_if_new

logger = logging.getLogger(__name__)

FORWARD_DELAY_SECONDS = 5


class ContentListener:
    """Reads new posts from public channels/groups the user account has joined
    and reposts them to the destination chat, in order, once each.

    Uses a Telethon user session (not the Bot API) because the Bot API can only
    read chats where the bot itself is an admin/member - not arbitrary channels
    the owner has personally joined.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = TelegramClient(
            settings.telethon_session_name,
            settings.telethon_api_id,
            settings.telethon_api_hash,
        )
        self._queue: asyncio.Queue = asyncio.Queue()

    async def start(self) -> None:
        await self.client.start()
        self.client.add_event_handler(
            self._on_new_message, events.NewMessage(chats=self.settings.source_chats)
        )
        asyncio.create_task(self._forward_worker())
        logger.info("Content listener watching: %s", self.settings.source_chats)

    async def _on_new_message(self, event) -> None:
        is_new = await record_content_if_new(self.settings.db_path, event.chat_id, event.id)
        if is_new:
            await self._queue.put(event.message)

    async def _forward_worker(self) -> None:
        # Single consumer serializes forwards so order is preserved and posts
        # are spaced out instead of arriving in a spammy burst.
        while True:
            message = await self._queue.get()
            try:
                await self.client.forward_messages(self.settings.destination_chat_id, message)
            except Exception:
                logger.exception("Failed to forward message %s", message.id)
            await asyncio.sleep(FORWARD_DELAY_SECONDS)

    async def run_forever(self) -> None:
        await self.client.run_until_disconnected()
