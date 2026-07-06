import logging
import re

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

from app.ai.router import AIRouter
from app.config import Settings
from app.db import (
    add_question,
    add_route,
    add_team_member,
    build_daily_report,
    customer_for_team,
    end_relay,
    get_chat_history,
    get_relay,
    list_questions,
    list_routes,
    list_team_members,
    remove_question,
    remove_route,
    remove_team_member,
    save_chat_message,
    set_customer_status,
    start_relay,
    touch_relay,
)

logger = logging.getLogger(__name__)


def _parse_id(message: Message) -> int | None:
    parts = (message.text or "").split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def _text_after_command(message: Message) -> str:
    parts = (message.text or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _customer_id_from_reply(message: Message) -> int | None:
    """If this message is a Telegram reply to a customer notice/relay the bot
    sent (which embed the customer id as '#42' or 'id 42'), return that id."""
    replied = message.reply_to_message
    if not replied or not replied.text:
        return None
    match = re.search(r"#(\d+)", replied.text) or re.search(r"\bid (\d+)", replied.text)
    return int(match.group(1)) if match else None


def build_admin_dispatcher(settings: Settings, ai: AIRouter, customer_bot: Bot) -> Dispatcher:
    dp = Dispatcher()

    def is_owner(message: Message) -> bool:
        return message.from_user is not None and message.from_user.id == settings.admin_telegram_id

    # ----- member moderation -----

    @dp.message(Command("approve"))
    async def on_approve(message: Message) -> None:
        if not is_owner(message):
            return
        user_id = _parse_id(message)
        if user_id is None:
            await message.answer("Usage: /approve <user_id>")
            return
        await customer_bot.approve_chat_join_request(settings.destination_chat_id, user_id)
        await set_customer_status(settings.db_path, user_id, "approved")
        await message.answer(f"Approved {user_id}.")

    @dp.message(Command("reject"))
    async def on_reject(message: Message) -> None:
        if not is_owner(message):
            return
        user_id = _parse_id(message)
        if user_id is None:
            await message.answer("Usage: /reject <user_id>")
            return
        await customer_bot.decline_chat_join_request(settings.destination_chat_id, user_id)
        await set_customer_status(settings.db_path, user_id, "removed", reason="manually rejected")
        await message.answer(f"Rejected {user_id}.")

    @dp.message(Command("remove"))
    async def on_remove(message: Message) -> None:
        if not is_owner(message):
            return
        user_id = _parse_id(message)
        if user_id is None:
            await message.answer("Usage: /remove <user_id>")
            return
        await customer_bot.ban_chat_member(settings.destination_chat_id, user_id)
        await set_customer_status(settings.db_path, user_id, "removed", reason="manually removed")
        await message.answer(f"Removed {user_id}.")

    # ----- content routes -----

    @dp.message(Command("addroute"))
    async def on_addroute(message: Message) -> None:
        if not is_owner(message):
            return
        parts = (message.text or "").split()
        if len(parts) < 3:
            await message.answer("Usage: /addroute <source> <destination>\nExample: /addroute @ind_crypto -1002146551577")
            return
        added = await add_route(settings.db_path, parts[1], parts[2])
        await message.answer(
            f"Route added: {parts[1]} -> {parts[2]} (live in ~30s)" if added else "That route already exists."
        )

    @dp.message(Command("routes"))
    async def on_routes(message: Message) -> None:
        if not is_owner(message):
            return
        routes = await list_routes(settings.db_path)
        if not routes:
            await message.answer("No routes yet. Add one with /addroute <source> <destination>.")
            return
        lines = ["Routes:"] + [f"#{r['id']}: {r['source_chat']} -> {r['destination_chat']}" for r in routes]
        lines.append("\nRemove with /delroute <id>.")
        await message.answer("\n".join(lines))

    @dp.message(Command("delroute"))
    async def on_delroute(message: Message) -> None:
        if not is_owner(message):
            return
        route_id = _parse_id(message)
        if route_id is None:
            await message.answer("Usage: /delroute <id>")
            return
        removed = await remove_route(settings.db_path, route_id)
        await message.answer(f"Removed route #{route_id}." if removed else f"No route #{route_id}.")

    # ----- intake questions -----

    @dp.message(Command("addquestion"))
    async def on_addquestion(message: Message) -> None:
        if not is_owner(message):
            return
        question = _text_after_command(message)
        if not question:
            await message.answer("Usage: /addquestion <your question>")
            return
        await add_question(settings.db_path, question)
        await message.answer("Question added. The assistant will weave it into chats.")

    @dp.message(Command("questions"))
    async def on_questions(message: Message) -> None:
        if not is_owner(message):
            return
        questions = await list_questions(settings.db_path)
        if not questions:
            await message.answer("No questions yet. Add one with /addquestion <text>.")
            return
        lines = ["Intake questions:"] + [f"#{q['id']}: {q['question']}" for q in questions]
        lines.append("\nRemove with /delquestion <id>.")
        await message.answer("\n".join(lines))

    @dp.message(Command("delquestion"))
    async def on_delquestion(message: Message) -> None:
        if not is_owner(message):
            return
        qid = _parse_id(message)
        if qid is None:
            await message.answer("Usage: /delquestion <id>")
            return
        removed = await remove_question(settings.db_path, qid)
        await message.answer(f"Removed question #{qid}." if removed else f"No question #{qid}.")

    # ----- team + live relay -----

    @dp.message(Command("addteam"))
    async def on_addteam(message: Message) -> None:
        if not is_owner(message):
            return
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("Usage: /addteam <telegram_id> <name>")
            return
        try:
            team_id = int(parts[1])
        except ValueError:
            await message.answer("The telegram id must be a number.")
            return
        await add_team_member(settings.db_path, team_id, parts[2])
        await message.answer(
            f"Added team member {parts[2]} ({team_id}). Ask them to open @{(await message.bot.me()).username} "
            f"and press Start so they can receive customers."
        )

    @dp.message(Command("team"))
    async def on_team(message: Message) -> None:
        if not is_owner(message):
            return
        members = await list_team_members(settings.db_path)
        if not members:
            await message.answer("No team members yet. Add one with /addteam <telegram_id> <name>.")
            return
        lines = ["Team:"] + [f"{m['name']} - {m['telegram_id']}" for m in members]
        lines.append("\nAssign a customer with /assign <customer_id> <team_id>.")
        await message.answer("\n".join(lines))

    @dp.message(Command("delteam"))
    async def on_delteam(message: Message) -> None:
        if not is_owner(message):
            return
        team_id = _parse_id(message)
        if team_id is None:
            await message.answer("Usage: /delteam <telegram_id>")
            return
        removed = await remove_team_member(settings.db_path, team_id)
        await message.answer(f"Removed team member {team_id}." if removed else f"No team member {team_id}.")

    @dp.message(Command("assign"))
    async def on_assign(message: Message) -> None:
        if not is_owner(message):
            return
        parts = (message.text or "").split()
        if len(parts) < 3:
            await message.answer("Usage: /assign <customer_id> <team_id>")
            return
        try:
            customer_id, team_id = int(parts[1]), int(parts[2])
        except ValueError:
            await message.answer("Both ids must be numbers.")
            return
        await start_relay(settings.db_path, customer_id, team_id)
        history = await get_chat_history(settings.db_path, customer_id, 30)
        transcript = "\n".join(
            f"{'Customer' if m['direction'] == 'in' else 'Bot/You'}: {m['text']}" for m in history
        ) or "(no history yet)"
        try:
            await message.bot.send_message(
                team_id,
                f"You are now handling customer #{customer_id}. Reply here to talk to them; "
                f"send /done when finished.\n\nRecent history:\n{transcript}",
            )
        except Exception:
            logger.exception("Failed to message team member")
            await message.answer(
                f"Assigned, but I couldn't message {team_id}. They must open this bot and press Start first."
            )
            return
        try:
            await customer_bot.send_message(customer_id, "You're now connected with a team member.")
        except Exception:
            logger.exception("Failed to notify customer of handoff")
        await message.answer(f"Customer #{customer_id} handed off to team member {team_id}.")

    @dp.message(Command("endrelay"))
    async def on_endrelay(message: Message) -> None:
        if not is_owner(message):
            return
        customer_id = _parse_id(message)
        if customer_id is None:
            await message.answer("Usage: /endrelay <customer_id>")
            return
        ended = await end_relay(settings.db_path, customer_id)
        await message.answer(f"Ended relay for #{customer_id}." if ended else f"No active relay for #{customer_id}.")

    # ----- owner takeover (hold the AI and reply yourself) -----

    @dp.message(Command("take"))
    async def on_take(message: Message) -> None:
        if not is_owner(message):
            return
        customer_id = _parse_id(message)
        if customer_id is None:
            await message.answer("Usage: /take <customer_id>  (pauses the AI so you reply yourself)")
            return
        await start_relay(settings.db_path, customer_id, settings.admin_telegram_id)
        history = await get_chat_history(settings.db_path, customer_id, 15)
        transcript = "\n".join(
            f"{'Customer' if m['direction'] == 'in' else 'Bot/You'}: {m['text']}" for m in history
        ) or "(no history yet)"
        await message.answer(
            f"You are now handling customer #{customer_id}; the AI is paused.\n"
            f"Reply with /say {customer_id} <message>. Let the AI resume with /release {customer_id}.\n\n"
            f"Recent history:\n{transcript}"
        )

    @dp.message(Command("say"))
    async def on_say(message: Message) -> None:
        if not is_owner(message):
            return
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("Usage: /say <customer_id> <message>")
            return
        try:
            customer_id = int(parts[1])
        except ValueError:
            await message.answer("Customer id must be a number.")
            return
        text = parts[2]
        try:
            # Auto-hold the AI if this customer isn't already being handled.
            if await get_relay(settings.db_path, customer_id) is None:
                await start_relay(settings.db_path, customer_id, settings.admin_telegram_id)
            await customer_bot.send_message(customer_id, text)
            await save_chat_message(settings.db_path, customer_id, "out", text)
            await touch_relay(settings.db_path, customer_id)
            await message.answer("Sent.")
        except Exception:
            logger.exception("Failed to /say to customer")
            await message.answer("Couldn't deliver that to the customer.")

    @dp.message(Command("release"))
    async def on_release(message: Message) -> None:
        if not is_owner(message):
            return
        customer_id = _parse_id(message)
        if customer_id is None:
            await message.answer("Usage: /release <customer_id>  (lets the AI take back over)")
            return
        ended = await end_relay(settings.db_path, customer_id)
        if ended:
            try:
                await customer_bot.send_message(customer_id, "You're now back with our assistant.")
            except Exception:
                logger.exception("Failed to notify customer of release")
        await message.answer(f"AI resumed for #{customer_id}." if ended else f"No active hold on #{customer_id}.")

    # ----- reports + help -----

    @dp.message(Command("report"))
    async def on_report(message: Message) -> None:
        if not is_owner(message):
            return
        stats = await build_daily_report(settings.db_path)
        raw = (
            f"Report for {stats['date']}:\n"
            f"- New members: {stats['new_members']}\n"
            f"- Pending approval: {stats['pending_members']}\n"
            f"- Suspicious: {stats['suspicious_members']}\n"
            f"- New-user messages: {stats['new_user_messages']}\n"
            f"- Old-user messages: {stats['old_user_messages']}\n"
            f"- Active chats: {stats['active_chats']}"
        )
        try:
            summary = await ai.generate_daily_report_summary(stats)
            await message.answer(f"{summary}\n\n{raw}")
        except Exception:
            logger.exception("Report AI summary failed - sending raw stats")
            await message.answer(raw)

    @dp.message(Command("help"))
    async def on_help(message: Message) -> None:
        if not is_owner(message):
            return
        await message.answer(
            "Commands:\n"
            "/report - daily stats\n"
            "Routes: /routes, /addroute <src> <dest>, /delroute <id>\n"
            "Questions: /questions, /addquestion <text>, /delquestion <id>\n"
            "Team: /team, /addteam <id> <name>, /delteam <id>\n"
            "Handoff: /assign <customer_id> <team_id>, /endrelay <customer_id>\n"
            "Members: /approve <id>, /reject <id>, /remove <id>\n"
            "Any other message - chat with your AI assistant"
        )

    # ----- catch-all: owner AI chat, or team-member relay -----

    @dp.message()
    async def on_chat(message: Message) -> None:
        if message.text is None:
            return
        uid = message.from_user.id
        text = message.text

        # Replying (Telegram reply) to a customer notice/relay = start handling
        # that customer: the reply goes to them and the AI auto-pauses.
        replied_customer = _customer_id_from_reply(message)
        if replied_customer is not None:
            if await get_relay(settings.db_path, replied_customer) is None:
                await start_relay(settings.db_path, replied_customer, uid)
            try:
                await customer_bot.send_message(replied_customer, text)
                await save_chat_message(settings.db_path, replied_customer, "out", text)
                await touch_relay(settings.db_path, replied_customer)
                await message.answer(f"Sent to #{replied_customer} (AI paused).")
            except Exception:
                logger.exception("Failed to send reply to customer")
                await message.answer("Couldn't deliver that to the customer.")
            return

        if uid != settings.admin_telegram_id:
            # A team member replying inside a live relay.
            customer_id = await customer_for_team(settings.db_path, uid)
            if customer_id is None:
                return
            if text.strip() == "/done":
                await end_relay(settings.db_path, customer_id)
                await message.answer(f"Ended your chat with customer #{customer_id}.")
                try:
                    await customer_bot.send_message(customer_id, "You're now back with our assistant.")
                except Exception:
                    logger.exception("Failed to notify customer relay ended")
                return
            try:
                await customer_bot.send_message(customer_id, text)
                await save_chat_message(settings.db_path, customer_id, "out", text)
                await touch_relay(settings.db_path, customer_id)
            except Exception:
                logger.exception("Failed to relay team message to customer")
                await message.answer("Couldn't deliver that to the customer.")
            return

        # Owner free-form AI assistant.
        try:
            reply = await ai.chat(
                [
                    {
                        "role": "system",
                        "content": "You are the owner's personal Telegram work assistant. Be direct and useful.",
                    },
                    {"role": "user", "content": text},
                ]
            )
        except Exception:
            logger.exception("Admin AI chat failed")
            reply = "Sorry, I couldn't reach the AI just now. Please try again."
        await message.answer(reply)

    return dp
