"""Reminder checking logic with gap protection, run via PTB's JobQueue.

The check runs every 60s. For each event we compute the notify slot for both
the current *and previous* local day, convert to UTC, and fire when a slot
falls inside the (last_check, now] window — so reminders survive downtime
that spans midnight (up to the 2-hour lookback cap).
"""

import logging
import html
from datetime import date, datetime, time, timedelta, timezone as dt_timezone

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import DEFAULT_REMINDER_DAYS
from db import get_all_events_with_timezone, get_event, get_system_setting, get_timezone, set_system_setting
from keyboard import build_snooze_inline
from utils import parse_iso_date, parse_reminder_days

logger = logging.getLogger(__name__)

LAST_CHECK_KEY = "last_reminder_check"
MAX_LOOKBACK = timedelta(hours=2)
INITIAL_LOOKBACK = timedelta(minutes=5)


def _build_message(name: str, days_until: int, years: int | None = None) -> str | None:
    """Build a reminder message based on how far away the event is."""
    safe_name = html.escape(str(name))
    if days_until == 0:
        msg = f"\U0001f389 Today is the day! Happy <b>{safe_name}</b>!"
        if years:
            msg += f" \U0001f382 ({years} years!)"
        return msg
    if days_until == 1:
        return f"\U0001f631 Get ready! <b>{safe_name}</b> is TOMORROW!"
    if days_until == 30:
        return f"\U0001f514 Head's up! <b>{safe_name}</b> is in 1 month."
    if 0 < days_until < 30 and days_until % 7 == 0:
        weeks = days_until // 7
        label = "week" if weeks == 1 else "weeks"
        return f"⏰ Reminder: <b>{safe_name}</b> is in {weeks} {label}."
    if days_until > 0:
        return f"⏰ Reminder: <b>{safe_name}</b> is in {days_until} days."
    return None


def _project_year_safe(event_dt: date, year: int) -> date:
    """Project a recurring date into a year, using Feb 28 for leap-day events."""
    try:
        return event_dt.replace(year=year)
    except ValueError:
        if event_dt.month == 2 and event_dt.day == 29:
            return event_dt.replace(year=year, day=28)
        raise


def _days_and_years(event_dt: date, recurring: bool, ref_date: date) -> tuple[int | None, int | None]:
    """(days_until, years elapsed) for an event relative to ref_date.

    Returns (None, None) for passed one-time events.
    """
    if recurring:
        occurrence = _project_year_safe(event_dt, ref_date.year)
        if occurrence < ref_date:
            occurrence = _project_year_safe(event_dt, ref_date.year + 1)
        years = occurrence.year - event_dt.year
        return (occurrence - ref_date).days, (years if years > 0 else None)
    days = (event_dt - ref_date).days
    if days < 0:
        return None, None
    return days, None


def compute_due_reminder(row: dict, utc_now: datetime, last_check: datetime) -> tuple[int, int | None] | None:
    """Return (days_until, years) if this event's notify slot fired in the window.

    Checks the notify slot for both the current and previous local day so
    catch-up works across midnight. Nonexistent local times (DST spring
    forward) resolve via PEP 495 fold semantics.
    """
    tz_name = row.get("timezone") or "UTC"
    try:
        user_tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        user_tz = ZoneInfo("UTC")

    try:
        notify_hour, notify_min = map(int, row["notify_time"].split(":"))
        time(notify_hour, notify_min)  # range check
    except (ValueError, AttributeError, TypeError):
        logger.warning("Malformed notify_time %r for event %r in chat %s",
                       row.get("notify_time"), row.get("name"), row.get("chat_id"))
        return None

    event_dt = parse_iso_date(row["event_date"])
    if event_dt is None:
        logger.warning("Malformed event_date %r for event %r in chat %s",
                       row.get("event_date"), row.get("name"), row.get("chat_id"))
        return None

    today_local = utc_now.astimezone(user_tz).date()
    for candidate_day in (today_local, today_local - timedelta(days=1)):
        notify_local = datetime.combine(candidate_day, time(notify_hour, notify_min), tzinfo=user_tz)
        notify_utc = notify_local.astimezone(dt_timezone.utc)
        if last_check < notify_utc <= utc_now:
            days_until, years = _days_and_years(
                event_dt, bool(row["recurring"]), candidate_day
            )
            if days_until is None:
                return None
            return days_until, years
    return None


def _allowed_days(row: dict) -> list[int]:
    """Per-event reminder day offsets, falling back to the default schedule."""
    raw = row.get("reminder_days")
    if raw:
        days = parse_reminder_days(raw)
        if days is not None:
            return days
    return DEFAULT_REMINDER_DAYS


def _get_last_check() -> datetime:
    """Get the last reminder check time, defaulting to a short lookback."""
    val = get_system_setting(LAST_CHECK_KEY)
    if val:
        try:
            dt = datetime.fromisoformat(val)
            if dt.tzinfo is not None:
                return dt
        except ValueError:
            pass
    return datetime.now(dt_timezone.utc) - INITIAL_LOOKBACK


def _set_last_check(dt: datetime):
    """Store the current check time."""
    set_system_setting(LAST_CHECK_KEY, dt.isoformat())


async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    """JobQueue callback: send reminders for any notify slot in the window."""
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
            due = compute_due_reminder(row, utc_now, last_check)
            if due is None:
                continue
            days_until, years = due
            if days_until not in _allowed_days(row):
                continue
            message = _build_message(row["name"], days_until, years)
            if not message:
                continue
            await context.bot.send_message(
                chat_id=row["chat_id"],
                text=message,
                parse_mode=ParseMode.HTML,
                reply_markup=build_snooze_inline(row["id"]),
            )
            logger.info("Sent reminder: %s (chat=%s, days=%s)", row["name"], row["chat_id"], days_until)
        except Exception:
            logger.exception("Error processing reminder for row %s", dict(row))

    _set_last_check(utc_now)


async def send_snoozed_reminder(context: ContextTypes.DEFAULT_TYPE):
    """JobQueue run_once callback for a snoozed reminder."""
    data = context.job.data
    chat_id, event_id = data["chat_id"], data["event_id"]

    ev = get_event(chat_id, event_id)
    if not ev:
        return  # event deleted meanwhile

    tz_name = get_timezone(chat_id)
    try:
        user_tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        user_tz = ZoneInfo("UTC")
    today = datetime.now(user_tz).date()

    event_dt = parse_iso_date(ev["event_date"])
    if event_dt is None:
        return
    days_until, years = _days_and_years(event_dt, bool(ev["recurring"]), today)
    if days_until is None:
        return

    message = _build_message(ev["name"], days_until, years)
    if message:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⏰ (Snoozed) {message}",
            parse_mode=ParseMode.HTML,
            reply_markup=build_snooze_inline(event_id),
        )


def start_scheduler(application):
    """Attach the reminder check to the application's JobQueue."""
    application.job_queue.run_repeating(check_reminders, interval=60, first=5)
    logger.info("Reminder scheduler started (every 60s via JobQueue)")
