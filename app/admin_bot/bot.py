import logging

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

from app.ai.router import AIRouter
from app.config import Settings
from app.db import (
    add_route,
    build_daily_report,
    list_routes,
    remove_route,
    set_customer_status,
)

logger = logging.getLogger(__name__)


def _parse_user_id(message: Message) -> int | None:
    parts = (message.text or "").split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def build_admin_dispatcher(settings: Settings, ai: AIRouter, customer_bot: Bot) -> Dispatcher:
    dp = Dispatcher()

    @dp.message(Command("approve"))
    async def on_approve(message: Message) -> None:
        user_id = _parse_user_id(message)
        if user_id is None:
            await message.answer("Usage: /approve <user_id>")
            return
        await customer_bot.approve_chat_join_request(settings.destination_chat_id, user_id)
        await set_customer_status(settings.db_path, user_id, "approved")
        await message.answer(f"Approved {user_id}.")

    @dp.message(Command("reject"))
    async def on_reject(message: Message) -> None:
        user_id = _parse_user_id(message)
        if user_id is None:
            await message.answer("Usage: /reject <user_id>")
            return
        await customer_bot.decline_chat_join_request(settings.destination_chat_id, user_id)
        await set_customer_status(settings.db_path, user_id, "removed", reason="manually rejected")
        await message.answer(f"Rejected {user_id}.")

    @dp.message(Command("remove"))
    async def on_remove(message: Message) -> None:
        user_id = _parse_user_id(message)
        if user_id is None:
            await message.answer("Usage: /remove <user_id>")
            return
        await customer_bot.ban_chat_member(settings.destination_chat_id, user_id)
        await set_customer_status(settings.db_path, user_id, "removed", reason="manually removed")
        await message.answer(f"Removed {user_id}.")

    @dp.message(Command("addroute"))
    async def on_addroute(message: Message) -> None:
        parts = (message.text or "").split()
        if len(parts) < 3:
            await message.answer(
                "Usage: /addroute <source> <destination>\n"
                "Example: /addroute @ind_crypto -1002146551577\n"
                "(source = channel you joined; destination = your channel id or @username)"
            )
            return
        source, destination = parts[1], parts[2]
        added = await add_route(settings.db_path, source, destination)
        if added:
            await message.answer(f"Route added: {source} -> {destination}\n(takes effect within ~30s)")
        else:
            await message.answer("That route already exists.")

    @dp.message(Command("routes"))
    async def on_routes(message: Message) -> None:
        routes = await list_routes(settings.db_path)
        if not routes:
            await message.answer("No routes yet. Add one with /addroute <source> <destination>.")
            return
        lines = ["Your routes:"]
        for r in routes:
            state = "" if r["active"] else " (paused)"
            lines.append(f"#{r['id']}: {r['source_chat']} -> {r['destination_chat']}{state}")
        lines.append("\nRemove one with /delroute <id>.")
        await message.answer("\n".join(lines))

    @dp.message(Command("delroute"))
    async def on_delroute(message: Message) -> None:
        route_id = _parse_user_id(message)
        if route_id is None:
            await message.answer("Usage: /delroute <id>  (see ids with /routes)")
            return
        removed = await remove_route(settings.db_path, route_id)
        await message.answer(f"Removed route #{route_id}." if removed else f"No route #{route_id} found.")

    @dp.message(Command("help"))
    async def on_help(message: Message) -> None:
        await message.answer(
            "Commands:\n"
            "/report - daily stats\n"
            "/routes - list content-copy routes\n"
            "/addroute <source> <destination> - add a copy route\n"
            "/delroute <id> - remove a route\n"
            "/approve <user_id> | /reject <user_id> | /remove <user_id>\n"
            "Any other message - chat with your AI assistant"
        )

    @dp.message(Command("report"))
    async def on_report(message: Message) -> None:
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

    @dp.message()
    async def on_chat(message: Message) -> None:
        if message.text is None:
            return
        try:
            reply = await ai.chat(
                [
                    {
                        "role": "system",
                        "content": "You are the owner's personal Telegram work assistant. Be direct and useful.",
                    },
                    {"role": "user", "content": message.text},
                ]
            )
        except Exception:
            logger.exception("Admin AI chat failed")
            reply = "Sorry, I couldn't reach the AI just now. Please try again."
        await message.answer(reply)

    return dp
