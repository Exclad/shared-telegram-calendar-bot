"""Background scheduler for reminder notifications with gap protection."""

import logging
from datetime import datetime, timedelta, timezone as dt_timezone

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.constants import ParseMode
from telegram.ext import Application

from db import get_all_events_with_timezone, get_system_setting, set_system_setting

logger = logging.getLogger(__name__)

LAST_CHECK_KEY = "last_reminder_check"
MAX_LOOKBACK = timedelta(hours=2)
INITIAL_LOOKBACK = timedelta(minutes=5)


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


def _get_last_check() -> datetime:
    """Get the last reminder check time, defaulting to a short lookback."""
    val = get_system_setting(LAST_CHECK_KEY)
    if val:
        try:
            return datetime.fromisoformat(val)
        except ValueError:
            pass
    return datetime.now(dt_timezone.utc) - INITIAL_LOOKBACK


def _set_last_check(dt: datetime):
    """Store the current check time."""
    set_system_setting(LAST_CHECK_KEY, dt.isoformat())


async def check_reminders(application: Application):
    """Check all events and send reminders for any notify_time in the window."""
    utc_now = datetime.now(dt_timezone.utc)
    last_check = _get_last_check()

    # Cap lookback to prevent spam after long downtime
    if utc_now - last_check > MAX_LOOKBACK:
        last_check = utc_now - MAX_LOOKBACK

    try:
        rows = get_all_events_with_timezone()
    except Exception:
        logger.exception("Failed to fetch events for reminder check")
        _set_last_check(utc_now)
        return

    for row in rows:
        try:
            await _check_single_event(application, row, utc_now, last_check)
        except Exception:
            logger.exception("Error processing reminder for row %s", dict(row))

    _set_last_check(utc_now)


async def _check_single_event(application, row, utc_now, last_check):
    chat_id = row["chat_id"]
    name = row["name"]
    date_str = row["event_date"]
    notify_time = row["notify_time"]
    recurring = bool(row["recurring"])

    tz_name = row["timezone"] if row["timezone"] else "UTC"
    try:
        user_tz = pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        user_tz = pytz.utc

    # Parse notify time
    notify_hour, notify_min = map(int, notify_time.split(":"))
    user_now = utc_now.astimezone(user_tz)
    user_last = last_check.astimezone(user_tz)

    # The notify time today in user's timezone
    notify_dt = user_now.replace(
        hour=notify_hour, minute=notify_min, second=0, microsecond=0
    )

    # Only fire if notify_dt falls in (last_check, utc_now]
    if not (user_last < notify_dt <= user_now):
        return

    # Parse event date and calculate days_until
    event_dt = datetime.strptime(date_str, "%d-%m-%Y").date()
    user_today = user_now.date()

    if recurring:
        this_year_event = event_dt.replace(year=user_today.year)
        if this_year_event < user_today:
            this_year_event = event_dt.replace(year=user_today.year + 1)
        days_until = (this_year_event - user_today).days
    else:
        # Non-recurring: use actual date, don't wrap
        days_until = (event_dt - user_today).days
        if days_until < 0:
            return  # event already passed

    message = _build_message(name, days_until)

    if message:
        await application.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=ParseMode.HTML
        )
        logger.info("Sent reminder: %s (chat=%s, days=%s)", name, chat_id, days_until)


def start_scheduler(application: Application):
    """Launch the background scheduler."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_reminders,
        "interval",
        seconds=60,
        args=[application],
    )
    scheduler.start()
    logger.info("Reminder scheduler started (every 60s)")
