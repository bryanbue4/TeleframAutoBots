import asyncio
import logging

from aiogram import Bot

from app.admin_bot.bot import build_admin_dispatcher
from app.ai.router import AIRouter
from app.config import load_settings
from app.content_sync.listener import ContentListener
from app.customer_bot.bot import build_customer_dispatcher
from app.db import init_db
from app.scheduler.jobs import build_scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    settings = load_settings()
    await init_db(settings.db_path)

    ai = AIRouter(settings.openrouter_api_key, settings.openrouter_model)

    customer_bot = Bot(token=settings.customer_bot_token)
    admin_bot = Bot(token=settings.admin_bot_token)

    customer_dp = build_customer_dispatcher(settings, ai, customer_bot, admin_bot)
    admin_dp = build_admin_dispatcher(settings, ai, customer_bot)

    listener = ContentListener(settings)
    await listener.start()

    scheduler = build_scheduler(customer_bot, admin_bot, ai, settings)
    scheduler.start()

    logger.info("All services started")
    await asyncio.gather(
        customer_dp.start_polling(customer_bot),
        admin_dp.start_polling(admin_bot),
        listener.run_forever(),
    )


if __name__ == "__main__":
    asyncio.run(main())
