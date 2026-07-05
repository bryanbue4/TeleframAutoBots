from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

from app.ai.router import AIRouter
from app.config import Settings
from app.db import build_daily_report, set_customer_status


def build_admin_dispatcher(settings: Settings, ai: AIRouter, customer_bot: Bot) -> Dispatcher:
    dp = Dispatcher()

    @dp.message(Command("approve"))
    async def on_approve(message: Message) -> None:
        user_id = int(message.text.split()[1])
        await customer_bot.approve_chat_join_request(settings.destination_chat_id, user_id)
        await set_customer_status(settings.db_path, user_id, "approved")
        await message.answer(f"Approved {user_id}.")

    @dp.message(Command("reject"))
    async def on_reject(message: Message) -> None:
        user_id = int(message.text.split()[1])
        await customer_bot.decline_chat_join_request(settings.destination_chat_id, user_id)
        await set_customer_status(settings.db_path, user_id, "removed", reason="manually rejected")
        await message.answer(f"Rejected {user_id}.")

    @dp.message(Command("remove"))
    async def on_remove(message: Message) -> None:
        user_id = int(message.text.split()[1])
        await customer_bot.ban_chat_member(settings.destination_chat_id, user_id)
        await set_customer_status(settings.db_path, user_id, "removed", reason="manually removed")
        await message.answer(f"Removed {user_id}.")

    @dp.message(Command("report"))
    async def on_report(message: Message) -> None:
        stats = await build_daily_report(settings.db_path)
        summary = await ai.generate_daily_report_summary(stats)
        await message.answer(summary)

    @dp.message()
    async def on_chat(message: Message) -> None:
        if message.text is None:
            return
        reply = await ai.chat(
            [
                {
                    "role": "system",
                    "content": "You are the owner's personal Telegram work assistant. Be direct and useful.",
                },
                {"role": "user", "content": message.text},
            ]
        )
        await message.answer(reply)

    return dp
