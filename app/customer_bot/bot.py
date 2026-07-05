import logging

from aiogram import Bot, Dispatcher, F
from aiogram.types import ChatJoinRequest, Message

from app.ai.router import AIRouter
from app.config import Settings
from app.db import (
    find_duplicate,
    flag_duplicate,
    flag_suspicious,
    record_message,
    set_customer_details,
    set_customer_status,
    upsert_join_request,
)

logger = logging.getLogger(__name__)

SUSPICIOUS_MARKERS = ("http://", "https://", "t.me/")
FALLBACK_REPLY = "Hi! Thanks for your message - I'll get back to you shortly."


def build_customer_dispatcher(settings: Settings, ai: AIRouter, bot: Bot, admin_bot: Bot) -> Dispatcher:
    dp = Dispatcher()

    async def notify_admin(text: str) -> None:
        # Never let an admin-notification failure break handling of a member's message.
        try:
            await admin_bot.send_message(settings.admin_telegram_id, text)
        except Exception:
            logger.exception("Failed to notify admin")

    async def ai_reply(user_text: str) -> str:
        try:
            return await ai.reply_as_customer_assistant(user_text)
        except Exception:
            logger.exception("AI reply failed - sending fallback")
            return FALLBACK_REPLY

    @dp.chat_join_request()
    async def on_join_request(event: ChatJoinRequest) -> None:
        await upsert_join_request(settings.db_path, event.from_user.id)

        if event.from_user.username is None:
            await flag_suspicious(settings.db_path, event.from_user.id, "no username at join time")
            await notify_admin(
                f"Join request from {event.from_user.full_name} (id {event.from_user.id}) has no "
                f"username - held for review. Reply /approve {event.from_user.id} or "
                f"/reject {event.from_user.id}."
            )
            return

        await bot.approve_chat_join_request(event.chat.id, event.from_user.id)
        await set_customer_status(settings.db_path, event.from_user.id, "approved")
        await notify_admin(
            f"Approved new member: {event.from_user.full_name} (id {event.from_user.id})."
        )

    @dp.message(F.text == "/start")
    async def on_start(message: Message) -> None:
        greeting = await ai_reply("A new member just said /start. Greet them warmly.")
        await message.answer(greeting)

    @dp.message(F.text)
    async def on_text(message: Message) -> None:
        await record_message(settings.db_path, message.from_user.id)
        text = message.text.strip()

        if any(marker in text.lower() for marker in SUSPICIOUS_MARKERS):
            await flag_suspicious(settings.db_path, message.from_user.id, "sent a link")
            await notify_admin(
                f"Suspicious message from {message.from_user.full_name} "
                f"(id {message.from_user.id}): {text[:200]}"
            )

        if "," in text and len(text.split(",")) == 2:
            name, city = (part.strip() for part in text.split(","))
            await set_customer_details(settings.db_path, message.from_user.id, name, city)
            duplicate_id = await find_duplicate(settings.db_path, name, city, message.from_user.id)
            if duplicate_id:
                await flag_duplicate(settings.db_path, message.from_user.id)
                await notify_admin(
                    f"Possible duplicate: {name} / {city} matches existing member id {duplicate_id} "
                    f"(new member id {message.from_user.id})."
                )
            await message.answer(f"Thanks {name}! Great to have someone from {city} here.")
            return

        await message.answer(await ai_reply(text))

    return dp
