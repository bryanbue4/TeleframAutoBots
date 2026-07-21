import glob
import random

from aiogram import Bot
from aiogram.types import FSInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.ai.router import AIRouter
from app.config import Settings
from app.db import all_active_customers, build_daily_report


def _parse_hm(value: str) -> tuple[int, int]:
    hour, minute = value.split(":")
    return int(hour), int(minute)


def _random_photo(kind: str) -> str | None:
    photos = glob.glob(f"assets/{kind}/*")
    return random.choice(photos) if photos else None


async def post_greeting(customer_bot: Bot, ai: AIRouter, settings: Settings, kind: str) -> None:
    text = await ai.generate_greeting_message(kind)
    photo_path = _random_photo(kind)
    if photo_path:
        await customer_bot.send_photo(settings.destination_chat_id, FSInputFile(photo_path), caption=text)
    else:
        await customer_bot.send_message(settings.destination_chat_id, text)


async def nudge_members(customer_bot: Bot, settings: Settings) -> None:
    for customer in await all_active_customers(settings.db_path):
        try:
            await customer_bot.send_message(
                customer["telegram_user_id"],
                "Hey! Just checking in - would love to hear from you. Reply anytime :)",
            )
        except Exception:
            continue


async def send_owner_report(admin_bot: Bot, ai: AIRouter, settings: Settings) -> None:
    stats = await build_daily_report(settings.db_path)
    summary = await ai.generate_daily_report_summary(stats)
    await admin_bot.send_message(settings.admin_telegram_id, summary)


def build_scheduler(customer_bot: Bot, admin_bot: Bot, ai: AIRouter, settings: Settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    morning = _parse_hm(settings.morning_time)
    night = _parse_hm(settings.night_time)

    for kind, (hour, minute) in (("morning", morning), ("night", night)):
        scheduler.add_job(
            post_greeting, CronTrigger(hour=hour, minute=minute), args=[customer_bot, ai, settings, kind]
        )
        scheduler.add_job(nudge_members, CronTrigger(hour=hour, minute=minute), args=[customer_bot, settings])
        scheduler.add_job(
            send_owner_report, CronTrigger(hour=hour, minute=minute), args=[admin_bot, ai, settings]
        )

    return scheduler
