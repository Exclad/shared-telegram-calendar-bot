"""Relationship Memory Bot — entry point."""

import logging

from telegram.ext import Application

from config import TOKEN
from db import init_db, migrate
from handlers import register_handlers
from scheduler import start_scheduler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def post_init(application: Application):
    """Called after the bot starts. Launches the reminder scheduler."""
    start_scheduler(application)


def main():
    init_db()
    migrate()

    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    register_handlers(application)

    logger.info("Bot is running...")
    application.run_polling()


if __name__ == "__main__":
    main()
