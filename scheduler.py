"""Background scheduler for reminder notifications."""

import logging
from datetime import datetime

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.constants import ParseMode
from telegram.ext import Application

from db import get_all_events_with_timezone

logger = logging.getLogger(__name__)


def _build_message(name: str, days_until: int) -> str | None:
    """Build a reminder message based on how far away the event is."""
    if days_until == 30:
        return f"\U0001f514 Head's up! <b>{name}</b> is in 1 month."
    if 0 < days_until < 30 and days_until % 7 == 0:
        weeks = days_until // 7
        return f"⏰ Reminder: <b>{name}</b> is in {weeks} week(s)."
    if days_until == 1:
        return f"\U0001f631 Get ready! <b>{name}</b> is TOMORROW!"
    if days_until == 0:
        return f"\U0001f389 Today is the day! Happy <b>{name}</b>!"
    return None


async def check_reminders(application: Application):
    """Check all events and send reminders if the time matches."""
    try:
        rows = get_all_events_with_timezone()
    except Exception:
        logger.exception("Failed to fetch events for reminder check")
        return

    utc_now = datetime.now(pytz.utc)

    for row in rows:
        try:
            chat_id = row["chat_id"]
            name = row["name"]
            date_str = row["event_date"]
            notify_time = row["notify_time"]

            tz_name = row["timezone"] if row["timezone"] else "UTC"
            try:
                user_tz = pytz.timezone(tz_name)
            except pytz.UnknownTimeZoneError:
                user_tz = pytz.utc

            user_now = utc_now.astimezone(user_tz)
            user_today = user_now.date()
            user_time_str = user_now.strftime("%H:%M")

            if user_time_str != notify_time:
                continue

            event_dt = datetime.strptime(date_str, "%d-%m-%Y").date()
            this_year_event = event_dt.replace(year=user_today.year)

            if this_year_event < user_today:
                this_year_event = event_dt.replace(year=user_today.year + 1)

            days_until = (this_year_event - user_today).days
            message = _build_message(name, days_until)

            if message:
                await application.bot.send_message(
                    chat_id=chat_id, text=message, parse_mode=ParseMode.HTML
                )
        except Exception:
            logger.exception("Error processing reminder for row %s", dict(row))


def start_scheduler(application: Application):
    """Launch the background scheduler. Called once on bot startup."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_reminders,
        "interval",
        seconds=60,
        args=[application],
    )
    scheduler.start()
    logger.info("Reminder scheduler started (every 60s)")
