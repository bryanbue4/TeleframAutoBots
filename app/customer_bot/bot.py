import logging

from aiogram import Bot, Dispatcher, F
from aiogram.types import ChatJoinRequest, ChatMemberUpdated, Message

from app.ai.router import AIRouter
from app.config import Settings
from app.db import (
    count_incoming,
    end_relay,
    find_duplicate,
    flag_duplicate,
    flag_suspicious,
    get_active_questions,
    record_message,
    relay_seconds_since_handler,
    save_chat_message,
    save_detected_channel,
    set_customer_details,
    set_customer_status,
    team_for_customer,
    upsert_join_request,
)

logger = logging.getLogger(__name__)

SUSPICIOUS_MARKERS = ("http://", "https://", "t.me/")
FALLBACK_REPLY = "Hi! Thanks for your message - I'll get back to you shortly."
# If the human handling a chat goes quiet this long while the customer keeps
# messaging, the AI automatically takes back over.
HANDOFF_INACTIVITY_SECONDS = 15 * 60


def build_customer_dispatcher(settings: Settings, ai: AIRouter, bot: Bot, admin_bot: Bot) -> Dispatcher:
    dp = Dispatcher()

    async def notify_admin(text: str) -> None:
        # Never let an admin-notification failure break handling of a member's message.
        try:
            await admin_bot.send_message(settings.admin_telegram_id, text)
        except Exception:
            logger.exception("Failed to notify admin")

    async def ai_reply(user_text: str, questions: list[str] | None = None) -> str:
        try:
            return await ai.reply_as_customer_assistant(user_text, questions=questions)
        except Exception:
            logger.exception("AI reply failed - sending fallback")
            return FALLBACK_REPLY

    async def ai_moderate(user_text: str) -> str:
        try:
            return await ai.moderate(user_text)
        except Exception:
            logger.exception("AI moderation failed - assuming SAFE")
            return "SAFE"

    @dp.my_chat_member()
    async def on_added_to_chat(event: ChatMemberUpdated) -> None:
        # When the bot is added to a new channel/group, tell the owner its id so
        # they can use it as a destination without hunting for the id manually.
        status = event.new_chat_member.status
        if status in ("administrator", "member"):
            chat = event.chat
            await save_detected_channel(settings.db_path, chat.id, chat.title or "", chat.type)
            await notify_admin(
                f"✅ I was added to '{chat.title}' ({chat.type}).\n"
                f"Channel ID: {chat.id}\n"
                f"Use it as a destination, e.g.  /addroute @source {chat.id}"
            )

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
        user = message.from_user
        text = message.text.strip()
        await save_chat_message(settings.db_path, user.id, "in", text)
        await record_message(settings.db_path, user.id)

        # If this customer is in a live relay (team member or owner), pass their
        # message straight through - unless the human has gone quiet too long,
        # in which case the AI automatically takes back over.
        team_id = await team_for_customer(settings.db_path, user.id)
        if team_id:
            idle = await relay_seconds_since_handler(settings.db_path, user.id)
            if idle is not None and idle > HANDOFF_INACTIVITY_SECONDS:
                await end_relay(settings.db_path, user.id)
                await notify_admin(
                    f"AI resumed for {user.full_name} (id {user.id}) - handler was idle "
                    f"{int(idle // 60)} min while the customer kept messaging."
                )
                # fall through to the normal AI flow below
            else:
                try:
                    await admin_bot.send_message(team_id, f"👤 {user.full_name} (#{user.id}): {text}")
                except Exception:
                    logger.exception("Failed to relay customer message to team member")
                return

        # Notify on a customer's very first message.
        if await count_incoming(settings.db_path, user.id) == 1:
            await notify_admin(f"New customer {user.full_name} (id {user.id}) messaged: {text[:200]}")

        # AI moderation + link check -> alert the admin on abusive/spam content.
        label = await ai_moderate(text)
        has_link = any(marker in text.lower() for marker in SUSPICIOUS_MARKERS)
        if label != "SAFE" or has_link:
            reason = label.lower() if label != "SAFE" else "sent a link"
            await flag_suspicious(settings.db_path, user.id, reason)
            await notify_admin(
                f"⚠️ ALERT [{reason.upper()}] from {user.full_name} "
                f"(id {user.id}): {text[:200]}\nRemove with /remove {user.id}"
            )

        if "," in text and len(text.split(",")) == 2:
            name, city = (part.strip() for part in text.split(","))
            await set_customer_details(settings.db_path, user.id, name, city)
            duplicate_id = await find_duplicate(settings.db_path, name, city, user.id)
            if duplicate_id:
                await flag_duplicate(settings.db_path, user.id)
                await notify_admin(
                    f"Possible duplicate: {name} / {city} matches existing member id {duplicate_id} "
                    f"(new member id {user.id})."
                )
            reply = f"Thanks {name}! Great to have someone from {city} here."
            await save_chat_message(settings.db_path, user.id, "out", reply)
            await message.answer(reply)
            return

        questions = await get_active_questions(settings.db_path)
        reply = await ai_reply(text, questions)
        await save_chat_message(settings.db_path, user.id, "out", reply)
        await message.answer(reply)

    return dp
