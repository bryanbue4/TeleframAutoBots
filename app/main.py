import asyncio
import logging

from aiogram import Bot
from aiogram.types import BotCommand

from app.admin_bot.bot import build_admin_dispatcher
from app.ai.router import AIRouter
from app.config import load_settings
from app.content_sync.listener import ContentListener
from app.customer_bot.bot import build_customer_dispatcher
from app.dashboard.server import run_dashboard
from app.db import init_db, seed_routes_if_empty
from app.scheduler.jobs import build_scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ADMIN_COMMANDS = [
    BotCommand(command="report", description="Daily stats report"),
    BotCommand(command="routes", description="List content-copy routes"),
    BotCommand(command="addroute", description="Add a route: /addroute <source> <destination>"),
    BotCommand(command="delroute", description="Remove a route: /delroute <id>"),
    BotCommand(command="questions", description="List intake questions"),
    BotCommand(command="addquestion", description="Add a question: /addquestion <text>"),
    BotCommand(command="delquestion", description="Remove a question: /delquestion <id>"),
    BotCommand(command="team", description="List team members"),
    BotCommand(command="addteam", description="Add team member: /addteam <id> <name>"),
    BotCommand(command="delteam", description="Remove team member: /delteam <id>"),
    BotCommand(command="assign", description="Hand off: /assign <customer_id> <team_id>"),
    BotCommand(command="endrelay", description="End a handoff: /endrelay <customer_id>"),
    BotCommand(command="take", description="Take over a chat (pause AI): /take <customer_id>"),
    BotCommand(command="say", description="Reply while holding: /say <customer_id> <message>"),
    BotCommand(command="release", description="Let AI resume: /release <customer_id>"),
    BotCommand(command="approve", description="Approve a member: /approve <user_id>"),
    BotCommand(command="reject", description="Reject a member: /reject <user_id>"),
    BotCommand(command="remove", description="Remove a member: /remove <user_id>"),
    BotCommand(command="diag", description="Check AI model + connection"),
    BotCommand(command="help", description="Show all commands"),
]

CUSTOMER_COMMANDS = [
    BotCommand(command="start", description="Start chatting"),
]


async def run_listener_safely(listener: ContentListener) -> None:
    """Run the Telethon content listener without letting its failures take the
    bots down. A bad/expired session string or any Telethon error is logged and
    contained here - the customer and admin bots keep running regardless.
    """
    try:
        await listener.start()
        logger.info("Content listener started")
        await listener.run_forever()
    except Exception:
        logger.exception(
            "Content listener failed - continuing without content copy. "
            "Check TELETHON_SESSION_STRING (it may have been revoked)."
        )


async def main() -> None:
    settings = load_settings()
    await init_db(settings.db_path)
    # Carry the original env-based source/destination over into the routes table
    # the first time, so existing behaviour continues with no manual setup.
    await seed_routes_if_empty(settings.db_path, settings.source_chats, str(settings.destination_chat_id))

    ai = AIRouter(settings.openrouter_api_key, settings.openrouter_model)

    customer_bot = Bot(token=settings.customer_bot_token)
    admin_bot = Bot(token=settings.admin_bot_token)

    customer_dp = build_customer_dispatcher(settings, ai, customer_bot, admin_bot)
    admin_dp = build_admin_dispatcher(settings, ai, customer_bot)

    scheduler = build_scheduler(customer_bot, admin_bot, ai, settings)
    scheduler.start()

    # Register command menus so the commands show up in Telegram's "/" menu.
    try:
        await admin_bot.set_my_commands(ADMIN_COMMANDS)
        await customer_bot.set_my_commands(CUSTOMER_COMMANDS)
    except Exception:
        logger.exception("Failed to set command menus")

    logger.info("Starting bots (content listener + dashboard run in the background)")
    await asyncio.gather(
        customer_dp.start_polling(customer_bot),
        admin_dp.start_polling(admin_bot),
        run_listener_safely(ContentListener(settings)),
        run_dashboard(settings),
    )


if __name__ == "__main__":
    asyncio.run(main())
