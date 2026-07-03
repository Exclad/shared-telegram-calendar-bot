"""Conversation handlers and command handlers for the bot."""

import calendar as calendar_mod
import html
import io
import json
import logging
import time as time_mod
from datetime import date, datetime, timedelta, timezone as dt_timezone
from functools import wraps

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from telegram import Update, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from telegram.error import NetworkError, Forbidden, Conflict

from config import (
    ALLOWED_USERS,
    DEFAULT_REMINDER_DAYS,
    MAX_IMPORT_FILE_SIZE,
    NAME,
    DATE,
    TIME,
    RECURRING,
    NOTE_TITLE,
    NOTE_CONTENT,
    DELETE_CHOICE,
    SET_TIMEZONE,
    EDIT_SELECT_ID,
    EDIT_SELECT_FIELD,
    EDIT_NEW_VALUE,
    IMPORT_FILE,
    INLINE_AWAIT_FIELD,
    INLINE_AWAIT_VALUE,
    JOURNEY_EVENT_STATE,
    UNAUTHORIZED_REPLY_COOLDOWN,
)
from db import (
    add_event,
    get_events,
    get_event,
    update_event,
    delete_event,
    add_note,
    get_notes,
    get_note,
    update_note,
    delete_note,
    get_timezone,
    set_timezone as db_set_timezone,
    get_journey_event,
    set_journey_event,
    get_journey_event_for_chat,
)
from keyboard import (
    get_main_keyboard,
    get_back_keyboard,
    get_timezone_keyboard,
    get_delete_choice_keyboard,
    get_event_field_keyboard,
    get_note_field_keyboard,
    get_recurring_keyboard,
    build_calendar_inline,
    build_event_list_inline,
    build_note_list_inline,
    build_photo_note_inline,
    build_confirm_delete_inline,
    build_edit_field_inline,
    build_pagination_nav,
)
from scheduler import send_snoozed_reminder
from utils import (
    DATE_INPUT_HINT,
    format_display,
    parse_iso_date,
    parse_recurring,
    parse_reminder_days,
    parse_time_hhmm,
    parse_user_date,
    to_iso,
)

logger = logging.getLogger(__name__)

TELEGRAM_MSG_LIMIT = 4000  # a little under the hard 4096 cap
MAX_PHOTO_NOTES_PER_VIEW = 10
PER_PAGE = 5


def secure_text(value: str) -> str:
    """Escape HTML characters in user-provided text."""
    return html.escape(str(value)) if value else ""


_unauthorized_last_reply: dict[int, float] = {}


def restricted(func):
    """Decorator: only allow users in ALLOWED_USERS list.

    Unauthorized users get at most one rejection reply per cooldown window
    so strangers can't use the bot as a reply machine.
    """

    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in ALLOWED_USERS:
            logger.warning("Unauthorized access attempt from user %s", user_id)
            now = time_mod.monotonic()
            last = _unauthorized_last_reply.get(user_id, 0.0)
            if now - last >= UNAUTHORIZED_REPLY_COOLDOWN:
                _unauthorized_last_reply[user_id] = now
                if update.message:
                    await update.message.reply_text("⛔️ Sorry, this is a private bot.")
                elif update.callback_query:
                    await update.callback_query.answer(
                        "⛔️ Sorry, this is a private bot.", show_alert=True
                    )
            elif update.callback_query:
                await update.callback_query.answer()
            return
        return await func(update, context, *args, **kwargs)

    return wrapped


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler with specific handling for known error types."""
    error = context.error

    if isinstance(error, Conflict):
        logger.warning(
            "Conflict error: another bot instance is polling. "
            "This instance will reconnect automatically."
        )
        return

    if isinstance(error, NetworkError):
        logger.warning("Network error (will retry): %s", error)
        return

    if isinstance(error, Forbidden):
        logger.error("Bot blocked by user: %s", error)
        return

    logger.error("Exception while handling an update:", exc_info=error)


def calculate_elapsed(start_date: datetime, today: datetime) -> tuple:
    """Accurate years/months/days calculation respecting variable month lengths.

    When borrowing a month, the anchor day is clamped to that month's length
    (relativedelta convention), so e.g. Jan 31 → Mar 1 is 1 month, 1 day.
    """
    years = today.year - start_date.year
    months = today.month - start_date.month
    days = today.day - start_date.day

    if days < 0:
        months -= 1
        prev_month = today.month - 1 if today.month > 1 else 12
        prev_year = today.year if today.month > 1 else today.year - 1
        anchor_day = min(start_date.day, calendar_mod.monthrange(prev_year, prev_month)[1])
        days = (today.date() - date(prev_year, prev_month, anchor_day)).days

    if months < 0:
        years -= 1
        months += 12

    return years, months, days


def _normalize_field_value(field: str, value: str) -> tuple[str | None, str]:
    """Validate + normalize a field value for edit/create.

    Returns (error_message, normalized_value). Date input is converted to ISO.
    """
    value = (value or "").strip()
    if field == "Name" or field == "Title":
        if not value:
            return f"{field} cannot be empty.", value
    elif field == "Date":
        parsed = parse_user_date(value)
        if parsed is None:
            return f"Invalid Date Format. Use {DATE_INPUT_HINT}.", value
        return None, to_iso(parsed)
    elif field == "Time":
        normalized = parse_time_hhmm(value)
        if normalized is None:
            return "Invalid Time Format. Use HH:MM.", value
        return None, normalized
    elif field == "Recurring":
        if parse_recurring(value) is None:
            return "Please reply Yes (recurring) or No (one-time).", value
    elif field == "Reminders":
        days = parse_reminder_days(value)
        if days is None:
            return (
                "Invalid reminder list. Send comma-separated day offsets, "
                "e.g. <code>30,7,1,0</code> (0 = on the day).",
                value,
            )
        return None, ",".join(str(d) for d in days)
    return None, value


def _project_year_safe(ev_date: date, year: int) -> date:
    """Project a recurring date into a year, using Feb 28 for leap-day events."""
    try:
        return ev_date.replace(year=year)
    except ValueError:
        if ev_date.month == 2 and ev_date.day == 29:
            return ev_date.replace(year=year, day=28)
        raise


def _next_event_date(ev: dict, today: date) -> date | None:
    """Return the next occurrence date for an event, or None if it has passed."""
    ev_date = parse_iso_date(ev["event_date"])
    if ev_date is None:
        return None
    if ev.get("recurring"):
        next_date = _project_year_safe(ev_date, today.year)
        if next_date < today:
            next_date = _project_year_safe(ev_date, today.year + 1)
        return next_date
    if ev_date < today:
        return None
    return ev_date


def _is_passed_one_time(ev: dict, today: date) -> bool:
    """Check if a one-time event has already passed relative to the chat's today."""
    if ev.get("recurring"):
        return False
    ev_date = parse_iso_date(ev["event_date"])
    if ev_date is None:
        return False
    return ev_date < today


def _chat_today(chat_id: int) -> date:
    """Today's date in the chat's configured timezone."""
    try:
        tz = ZoneInfo(get_timezone(chat_id))
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    return datetime.now(tz).date()


def _reminders_display(ev: dict) -> str:
    raw = ev.get("reminder_days")
    if raw and parse_reminder_days(raw) is not None:
        return raw
    return "default (" + ",".join(str(d) for d in DEFAULT_REMINDER_DAYS) + ")"


# ── Basic commands ──────────────────────────────────────

@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "\U0001f44b <b>Relationship Memory Bot</b>\n\n"
        "Track dates, notes, photos, and reminders from the menu below.",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard(),
    )


@restricted
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "❓ <b>Help & Commands</b>\n\n"
        "➕ <b>Add Date</b> - save an event and reminder time\n"
        "📅 <b>List Dates</b> - view, edit, or delete dates\n"
        "➕ <b>Add Note</b> - save text or a photo\n"
        "📝 <b>View Notes</b> - view saved notes\n"
        "❤️ <b>Our Journey</b> - time since your chosen event\n"
        "🔍 <b>Upcoming</b> - events in the next 3 months\n"
        "🌍 <b>Set Timezone</b> - reminder timezone\n"
        "📤 <b>Export</b> - backup of all data (text + JSON file)\n\n"
        "<b>Commands</b>\n"
        "/start — show main menu\n"
        "/help — show this message\n"
        "/add — add a new date\n"
        "/addnote — add a new note\n"
        "/upcoming — upcoming events\n"
        "/days <i>name</i> — countdown to an event\n"
        "/export — export all data\n"
        "/import — restore from a JSON backup\n"
        "/timezone — set timezone\n"
        "/journey — change journey event\n"
        "/delete — delete an item\n"
        "/cancel — cancel current operation\n\n"
        "<b>Per-event reminders:</b> edit an event's <b>Reminders</b> field with "
        "comma-separated day offsets, e.g. <code>30,7,1,0</code>."
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard()
    )


async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "\U0001f519 Returned to Main Menu.", reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END


async def cancel_and_retry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback for menu actions that start a new flow: cancel and prompt retry."""
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Previous operation cancelled. Press the button again to start.",
        reply_markup=get_main_keyboard(),
    )
    return ConversationHandler.END


async def end_conversation_silently(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback for menu actions handled globally (group 1): just end the
    conversation so the global handler produces the single reply."""
    context.user_data.clear()
    return ConversationHandler.END


# ── Our Journey ─────────────────────────────────────────

@restricted
async def our_journey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    date_iso, event_name = get_journey_event_for_chat(chat_id)

    if not date_iso:
        await update.message.reply_text(
            "\U0001f494 I don't know when you started!\n\n"
            f"Please add an event named <b>{secure_text(event_name)}</b> so I can calculate your time together.\n\n"
            "You can change which event to use with ⚙️ Journey Event.",
            parse_mode=ParseMode.HTML,
        )
        return

    start_day = parse_iso_date(date_iso)
    if start_day is None:
        await update.message.reply_text(
            "Error calculating date. Please check your date format."
        )
        return

    start_date = datetime.combine(start_day, datetime.min.time(), tzinfo=dt_timezone.utc)
    today = datetime.now(dt_timezone.utc)

    if start_date > today:
        days_to_go = (start_day - today.date()).days
        await update.message.reply_text(
            f"\U0001f4ab <b>{secure_text(event_name)}</b> is in the future — "
            f"your journey starts in <b>{days_to_go}</b> day(s)!",
            parse_mode=ParseMode.HTML,
        )
        return

    years, months, days = calculate_elapsed(start_date, today)
    total_days = (today - start_date).days

    msg = (
        f"❤️ <b>Our Journey Together</b> ❤️\n\n"
        f"Since <b>{format_display(start_day)}</b>\n"
        f"We have been together for:\n"
        f"<b>{years}</b> Years, <b>{months}</b> Months, and <b>{days}</b> Days.\n\n"
        f"That is <b>{total_days}</b> days of love! \U0001f618"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


# ── Journey Event Config ────────────────────────────────

@restricted
async def journey_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    current = get_journey_event(chat_id)
    await update.message.reply_text(
        f"⚙️ The current journey event is: <b>{secure_text(current)}</b>\n\n"
        "Enter the exact name of the event you want to use for the ❤️ Our Journey calculation.\n"
        "For example, if you have an event named 'Wedding', type: Wedding",
        parse_mode=ParseMode.HTML,
        reply_markup=get_back_keyboard(),
    )
    return JOURNEY_EVENT_STATE


async def save_journey_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    event_name = update.message.text.strip()
    chat_id = update.effective_chat.id

    # Validate an event with this name exists
    events = get_events(chat_id)
    match = next((e for e in events if e["name"].lower() == event_name.lower()), None)
    if not match:
        event_list = ", ".join(f"<b>{secure_text(e['name'])}</b>" for e in events) if events else "(none)"
        await update.message.reply_text(
            f"❌ No event named <b>{secure_text(event_name)}</b> found.\n\n"
            f"Your events: {event_list}\n\nTry again or press Back.",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(),
        )
        return JOURNEY_EVENT_STATE

    set_journey_event(chat_id, match["id"], match["name"])

    await update.message.reply_text(
        f"✅ Journey event set to <b>{secure_text(match['name'])}</b>.",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard(),
    )
    return ConversationHandler.END


# ── Timezone ────────────────────────────────────────────

@restricted
async def timezone_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "\U0001f30d <b>Select your timezone</b>\n\n"
        "This ensures you get alerts at the correct time.\n"
        "Choose a button or type your timezone, e.g. <code>Asia/Tokyo</code>.",
        parse_mode=ParseMode.HTML,
        reply_markup=get_timezone_keyboard(),
    )
    return SET_TIMEZONE


async def save_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_tz = update.message.text.strip()

    if user_tz not in available_timezones():
        await update.message.reply_text(
            "❌ Invalid Timezone.\n"
            "Please choose from the buttons or check spelling (Case Sensitive, e.g., 'Asia/Singapore').",
            reply_markup=get_back_keyboard(),
        )
        return SET_TIMEZONE

    db_set_timezone(update.effective_chat.id, user_tz)

    await update.message.reply_text(
        f"✅ Timezone set to <b>{secure_text(user_tz)}</b>.",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard(),
    )
    return ConversationHandler.END


# ── Add Event ───────────────────────────────────────────

@restricted
async def add_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "What is the name of the event? (e.g., Anniversary)",
        reply_markup=get_back_keyboard(),
    )
    return NAME


async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Name cannot be empty. Try again.")
        return NAME
    context.user_data["event_name"] = name
    today = _chat_today(update.effective_chat.id)
    await update.message.reply_text(
        f"Great! Pick the date below, or type it ({DATE_INPUT_HINT}):",
        reply_markup=build_calendar_inline(today.year, today.month),
    )
    return DATE


async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parsed = parse_user_date(update.message.text)
    if parsed is None:
        await update.message.reply_text(f"Invalid format. Please use {DATE_INPUT_HINT}.")
        return DATE
    context.user_data["event_date"] = to_iso(parsed)
    await update.message.reply_text(
        "Date saved! Now, what time to remind? (Format: HH:MM)\nType 'skip' for 12:00 PM."
    )
    return TIME


async def calendar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline calendar interaction during the Add Date flow."""
    query = update.callback_query
    parts = query.data.split("|")
    action = parts[1]

    if action == "noop":
        await query.answer()
        return DATE

    if action == "nav":
        year, month = int(parts[2]), int(parts[3])
        if year < 1900 or year > 2200:
            await query.answer("Out of range.")
            return DATE
        await query.answer()
        await query.edit_message_reply_markup(
            reply_markup=build_calendar_inline(year, month)
        )
        return DATE

    # action == "pick"
    year, month, day = int(parts[2]), int(parts[3]), int(parts[4])
    try:
        picked = date(year, month, day)
    except ValueError:
        await query.answer("Invalid date.")
        return DATE
    await query.answer()
    context.user_data["event_date"] = to_iso(picked)
    await query.edit_message_text(f"📅 Date set: {format_display(picked)}")
    await update.effective_chat.send_message(
        "Now, what time to remind? (Format: HH:MM)\nType 'skip' for 12:00 PM.",
        reply_markup=get_back_keyboard(),
    )
    return TIME


async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    time_text = update.message.text.strip()
    if time_text.lower() == "skip":
        context.user_data["notify_time"] = "12:00"
    else:
        normalized = parse_time_hhmm(time_text)
        if normalized is None:
            await update.message.reply_text("Invalid format. Use HH:MM or type 'skip'.")
            return TIME
        context.user_data["notify_time"] = normalized

    await update.message.reply_text(
        "Is this a recurring event (yearly) or one-time?",
        reply_markup=get_recurring_keyboard(),
    )
    return RECURRING


async def get_recurring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    recurring = parse_recurring(update.message.text)
    if recurring is None:
        await update.message.reply_text(
            "Please choose Yes (Recurring) or No (One-time).",
            reply_markup=get_recurring_keyboard(),
        )
        return RECURRING

    add_event(
        update.effective_chat.id,
        context.user_data["event_name"],
        context.user_data["event_date"],
        context.user_data["notify_time"],
        recurring=recurring,
    )

    label = "Recurring" if recurring else "One-time"
    await update.message.reply_text(
        f"✅ Saved: <b>{secure_text(context.user_data['event_name'])}</b> "
        f"on {format_display(context.user_data['event_date'])} ({label})!",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard(),
    )
    return ConversationHandler.END


# ── Add Note ────────────────────────────────────────────

@restricted
async def add_note_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "\U0001f4dd New Note: What is the <b>Title</b>?",
        parse_mode=ParseMode.HTML,
        reply_markup=get_back_keyboard(),
    )
    return NOTE_TITLE


async def get_note_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    if not title:
        await update.message.reply_text("Title cannot be empty. Try again.")
        return NOTE_TITLE
    context.user_data["note_title"] = title
    await update.message.reply_text(
        "Got it. Send <b>Text</b> or a <b>Photo</b>.", parse_mode=ParseMode.HTML
    )
    return NOTE_CONTENT


async def get_note_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_id = None
    content = ""

    if update.message.photo:
        photo_id = update.message.photo[-1].file_id
        if update.message.caption:
            content = update.message.caption
    else:
        content = update.message.text or ""

    add_note(update.effective_chat.id, context.user_data["note_title"], content, photo_id)

    await update.message.reply_text(
        "✅ Note saved!", reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END


# ── List Events ─────────────────────────────────────────

def _build_event_page(chat_id: int, user_tz: str, page: int) -> tuple[str, int, list[dict]]:
    """Build paginated event list. Returns (message, total_pages, page_events)."""
    all_events = get_events(chat_id)
    today = _chat_today(chat_id)
    # Filter passed one-time events
    active = [e for e in all_events if not _is_passed_one_time(e, today)]
    total = max(1, (len(active) + PER_PAGE - 1) // PER_PAGE)
    page = min(page, total)
    start = (page - 1) * PER_PAGE
    page_events = active[start:start + PER_PAGE]

    msg = (
        f"\U0001f4c5 <b>Your Important Dates:</b>\n"
        f"<code>{secure_text(user_tz)}</code> · Page {page}/{total}\n\n"
    )
    for ev in page_events:
        label = "♻️" if ev.get("recurring") else "1️⃣"
        msg += (
            f"{label} <b>{secure_text(ev['name'])}</b>\n"
            f"   <code>{format_display(ev['event_date'])}</code> at <code>{secure_text(ev['notify_time'])}</code>\n"
        )
    if len(all_events) > len(active):
        msg += "\n<i>Passed one-time events are hidden.</i>"
    return msg, total, page_events


@restricted
async def list_events(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
    chat_id = update.effective_chat.id
    user_tz = get_timezone(chat_id)
    msg, total, page_events = _build_event_page(chat_id, user_tz, page)

    if not page_events and page == 1:
        await update.message.reply_text(
            "No active dates saved yet!", reply_markup=get_main_keyboard()
        )
        return

    item_kb = build_event_list_inline(page_events)
    nav_rows = build_pagination_nav(page, total, "ev")
    keyboard = InlineKeyboardMarkup(
        list(item_kb.inline_keyboard if item_kb else []) + nav_rows
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=keyboard)


@restricted
async def page_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback: navigate event list pages."""
    query = update.callback_query
    await query.answer()
    target_page = int(query.data.split("|")[2])
    chat_id = update.effective_chat.id
    user_tz = get_timezone(chat_id)
    msg, total, page_events = _build_event_page(chat_id, user_tz, target_page)

    item_kb = build_event_list_inline(page_events)
    nav_rows = build_pagination_nav(target_page, total, "ev")
    keyboard = InlineKeyboardMarkup(
        list(item_kb.inline_keyboard if item_kb else []) + nav_rows
    )
    await query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=keyboard)


# ── List Notes ──────────────────────────────────────────

def _build_note_page(chat_id: int, page: int) -> tuple[str, int, list[dict]]:
    """Build paginated text-note list. Returns (message, total_pages, page_notes)."""
    all_notes = get_notes(chat_id)
    text_notes = [n for n in all_notes if not n["photo_id"]]
    total = max(1, (len(text_notes) + PER_PAGE - 1) // PER_PAGE)
    page = min(page, total)
    start = (page - 1) * PER_PAGE
    page_notes = text_notes[start:start + PER_PAGE]

    msg = f"\U0001f4dd <b>Your Saved Notes:</b> Page {page}/{total}\n\n"
    for note in page_notes:
        msg += f"\U0001f4cc <b>{secure_text(note['title'])}</b>\n"
        if note["content"]:
            msg += f"<code>{secure_text(note['content'])}</code>\n\n"
    return msg, total, page_notes


@restricted
async def list_notes(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
    chat_id = update.effective_chat.id
    all_notes = get_notes(chat_id)
    text_notes = [n for n in all_notes if not n["photo_id"]]
    image_notes = [n for n in all_notes if n["photo_id"]]

    if not all_notes:
        await update.message.reply_text(
            "No notes saved yet!", reply_markup=get_main_keyboard()
        )
        return

    # Text notes — paginated (skipped entirely when there are none)
    if text_notes:
        msg, total, page_notes = _build_note_page(chat_id, page)
        inline_kb = build_note_list_inline(page_notes)
        nav_rows = build_pagination_nav(page, total, "nt")
        keyboard = InlineKeyboardMarkup(
            list(inline_kb.inline_keyboard if inline_kb else []) + nav_rows
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    # Photo notes — each with its own Edit/Del buttons, capped per view
    for note in image_notes[:MAX_PHOTO_NOTES_PER_VIEW]:
        caption = f"\U0001f4cc <b>{secure_text(note['title'])}</b>"
        if note["content"]:
            caption += f"\n{secure_text(note['content'])}"
        await update.message.reply_photo(
            photo=note["photo_id"],
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=build_photo_note_inline(note),
        )
    if len(image_notes) > MAX_PHOTO_NOTES_PER_VIEW:
        await update.message.reply_text(
            f"📷 Showing {MAX_PHOTO_NOTES_PER_VIEW} of {len(image_notes)} photo notes. "
            "Use ✏️ Edit Note / 🗑 Delete Item with IDs from 📤 Export to manage the rest.",
        )


@restricted
async def page_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback: navigate note list pages."""
    query = update.callback_query
    await query.answer()
    target_page = int(query.data.split("|")[2])
    chat_id = update.effective_chat.id
    msg, total, page_notes = _build_note_page(chat_id, target_page)

    inline_kb = build_note_list_inline(page_notes)
    nav_rows = build_pagination_nav(target_page, total, "nt")
    keyboard = InlineKeyboardMarkup(
        list(inline_kb.inline_keyboard if inline_kb else []) + nav_rows
    )
    await query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=keyboard)


# ── Upcoming / countdown ────────────────────────────────

@restricted
async def upcoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    events = get_events(update.effective_chat.id)
    user_tz = get_timezone(update.effective_chat.id)
    today = _chat_today(update.effective_chat.id)

    upcoming_list: list[tuple[str, str, int]] = []  # (name, date_str, days_until)

    for ev in events:
        next_date = _next_event_date(ev, today)
        if next_date is None:
            continue
        days_until = (next_date - today).days
        if days_until <= 90:
            upcoming_list.append((ev["name"], format_display(next_date), days_until))

    upcoming_list.sort(key=lambda x: x[2])

    if not upcoming_list:
        await update.message.reply_text(
            "No events in the next 3 months.",
            reply_markup=get_main_keyboard(),
        )
        return

    msg = f"🔍 <b>Upcoming Events</b>\n<code>{secure_text(user_tz)}</code> · next 3 months\n\n"
    for name, date_str, days in upcoming_list:
        when = f"in {days} day(s)" if days > 0 else "TODAY!"
        msg += f"• <b>{secure_text(name)}</b> · <code>{date_str}</code> · {when}\n"

    await update.message.reply_text(
        msg, parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard()
    )


@restricted
async def days_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/days <name> — countdown to matching event(s)."""
    query = " ".join(context.args or []).strip().lower()
    if not query:
        await update.message.reply_text(
            "Usage: /days <event name>\nExample: /days anniversary"
        )
        return

    events = get_events(update.effective_chat.id)
    today = _chat_today(update.effective_chat.id)
    matches = [e for e in events if query in e["name"].lower()]

    if not matches:
        await update.message.reply_text(f"No event matching '{query}' found.")
        return

    lines = []
    for ev in matches:
        next_date = _next_event_date(ev, today)
        if next_date is None:
            lines.append(f"• <b>{secure_text(ev['name'])}</b> — already passed")
            continue
        days = (next_date - today).days
        when = "is <b>TODAY</b>! 🎉" if days == 0 else f"in <b>{days}</b> day(s)"
        lines.append(
            f"• <b>{secure_text(ev['name'])}</b> · <code>{format_display(next_date)}</code> · {when}"
        )

    await update.message.reply_text(
        "⏳ <b>Countdown</b>\n\n" + "\n".join(lines), parse_mode=ParseMode.HTML
    )


# ── Export / Import ─────────────────────────────────────

def _chunk_message(msg: str, limit: int = TELEGRAM_MSG_LIMIT) -> list[str]:
    """Split a long message on line boundaries to fit Telegram's size cap."""
    if len(msg) <= limit:
        return [msg]
    chunks, current = [], ""
    for line in msg.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            if current:
                chunks.append(current)
            # A single pathological line still gets hard-split
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


@restricted
async def export_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    events = get_events(chat_id)
    notes = get_notes(chat_id)
    user_tz = get_timezone(chat_id)

    msg = "📤 <b>Your Exported Data</b>\n\n"
    msg += f"Timezone: <code>{secure_text(user_tz)}</code>\n\n"

    msg += "─── Dates ───\n"
    if events:
        for ev in events:
            label = "♻️" if ev.get("recurring") else "1️⃣"
            msg += (
                f"{label} [{ev['id']}] {secure_text(ev['name'])}: "
                f"{format_display(ev['event_date'])} at {secure_text(ev['notify_time'])}\n"
            )
    else:
        msg += "(none)\n"

    msg += "\n─── Notes ───\n"
    if notes:
        for n in notes:
            msg += f"📌 [{n['id']}] {secure_text(n['title'])}"
            if n["content"]:
                msg += f": {secure_text(n['content'])}"
            if n["photo_id"]:
                msg += " [photo — view in Telegram to see]"
            msg += "\n"
    else:
        msg += "(none)\n"

    for chunk in _chunk_message(msg):
        await update.message.reply_text(
            chunk, parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard()
        )

    # JSON backup file (restorable via /import)
    backup = {
        "version": 1,
        "timezone": user_tz,
        "events": [
            {
                "name": ev["name"],
                "date": ev["event_date"],
                "notify_time": ev["notify_time"],
                "recurring": bool(ev.get("recurring")),
                "reminder_days": ev.get("reminder_days"),
            }
            for ev in events
        ],
        "notes": [
            {
                "title": n["title"],
                "content": n["content"] or "",
                "photo_id": n["photo_id"],
            }
            for n in notes
        ],
    }
    buf = io.BytesIO(json.dumps(backup, indent=2, ensure_ascii=False).encode("utf-8"))
    buf.name = f"relationship-bot-backup-{datetime.now(dt_timezone.utc):%Y%m%d}.json"
    await update.message.reply_document(
        document=buf,
        caption="💾 JSON backup — restore any time with /import",
    )


@restricted
async def import_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📥 Send me a JSON backup file created by 📤 Export.\n"
        "Imported items are <b>added</b> to your existing data (no overwrite).",
        parse_mode=ParseMode.HTML,
        reply_markup=get_back_keyboard(),
    )
    return IMPORT_FILE


async def import_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc:
        await update.message.reply_text(
            "Please send a .json backup file, or press Back to cancel.",
            reply_markup=get_back_keyboard(),
        )
        return IMPORT_FILE

    if doc.file_size and doc.file_size > MAX_IMPORT_FILE_SIZE:
        await update.message.reply_text(
            "❌ File too large (max 256 KB).", reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

    tg_file = await doc.get_file()
    raw = bytes(await tg_file.download_as_bytearray())

    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except (ValueError, UnicodeDecodeError):
        await update.message.reply_text(
            "❌ That file is not valid JSON. Send a backup created by 📤 Export.",
            reply_markup=get_back_keyboard(),
        )
        return IMPORT_FILE

    chat_id = update.effective_chat.id
    imported_events = imported_notes = skipped = 0

    for ev in data.get("events", []):
        try:
            name = str(ev["name"]).strip()
            raw_date = str(ev["date"])
            parsed = parse_iso_date(raw_date) or parse_user_date(raw_date)
            notify = parse_time_hhmm(str(ev.get("notify_time", "12:00"))) or "12:00"
            if not name or parsed is None:
                raise ValueError("bad event")
            reminder_days = ev.get("reminder_days")
            if reminder_days is not None:
                if parse_reminder_days(str(reminder_days)) is None:
                    reminder_days = None
                else:
                    reminder_days = str(reminder_days)
            add_event(chat_id, name, to_iso(parsed), notify,
                      recurring=bool(ev.get("recurring", True)),
                      reminder_days=reminder_days)
            imported_events += 1
        except (KeyError, TypeError, ValueError):
            skipped += 1

    for n in data.get("notes", []):
        try:
            title = str(n["title"]).strip()
            if not title:
                raise ValueError("bad note")
            photo_id = n.get("photo_id")
            add_note(chat_id, title, str(n.get("content", "") or ""),
                     str(photo_id) if photo_id else None)
            imported_notes += 1
        except (KeyError, TypeError, ValueError):
            skipped += 1

    summary = (
        f"✅ Import complete: <b>{imported_events}</b> event(s), "
        f"<b>{imported_notes}</b> note(s)."
    )
    if skipped:
        summary += f"\n⚠️ Skipped <b>{skipped}</b> invalid item(s)."
    await update.message.reply_text(
        summary, parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END


# ── Snooze ──────────────────────────────────────────────

@restricted
async def snooze_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reschedule a reminder for one hour later."""
    query = update.callback_query
    event_id = int(query.data.split("|")[1])
    chat_id = update.effective_chat.id

    if not get_event(chat_id, event_id):
        await query.answer("That event no longer exists.", show_alert=True)
        return

    context.job_queue.run_once(
        send_snoozed_reminder,
        when=timedelta(hours=1),
        data={"chat_id": chat_id, "event_id": event_id},
        name=f"snooze-{chat_id}-{event_id}",
    )
    await query.answer("Snoozed — I'll remind you again in 1 hour ⏰")


# ── Delete ──────────────────────────────────────────────

@restricted
async def delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("delete_confirm", None)
    await update.message.reply_text(
        "What would you like to delete?",
        reply_markup=get_delete_choice_keyboard(),
    )
    return DELETE_CHOICE


async def delete_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "Delete Date":
        context.user_data["delete_type"] = "event"
        context.user_data.pop("delete_confirm", None)
        events = get_events(update.effective_chat.id)
        if not events:
            await update.message.reply_text(
                "No dates to delete.", reply_markup=get_back_keyboard()
            )
            return DELETE_CHOICE

        msg = "\U0001f5d1 <b>Reply with the ID to delete:</b>\n\n"
        for ev in events:
            msg += (
                f"ID: <b>{ev['id']}</b> | {secure_text(ev['name'])} "
                f"({format_display(ev['event_date'])})\n"
            )
        await update.message.reply_text(
            msg, parse_mode=ParseMode.HTML, reply_markup=get_back_keyboard()
        )
        return DELETE_CHOICE

    elif text == "Delete Note":
        context.user_data["delete_type"] = "note"
        context.user_data.pop("delete_confirm", None)
        notes = get_notes(update.effective_chat.id)
        if not notes:
            await update.message.reply_text(
                "No notes to delete.", reply_markup=get_back_keyboard()
            )
            return DELETE_CHOICE

        msg = "\U0001f5d1 <b>Reply with the ID to delete:</b>\n\n"
        for n in notes:
            msg += f"ID: <b>{n['id']}</b> | {secure_text(n['title'])}\n"
        await update.message.reply_text(
            msg, parse_mode=ParseMode.HTML, reply_markup=get_back_keyboard()
        )
        return DELETE_CHOICE

    try:
        item_id = int(text)
        delete_type = context.user_data.get("delete_type")
        if delete_type not in ("event", "note"):
            await update.message.reply_text(
                "Choose Delete Date or Delete Note first.",
                reply_markup=get_delete_choice_keyboard(),
            )
            return DELETE_CHOICE

        # Confirmation step: type + id must both match the pending confirmation
        if context.user_data.get("delete_confirm") != (delete_type, item_id):
            context.user_data["delete_confirm"] = (delete_type, item_id)
            label = "date" if delete_type == "event" else "note"
            await update.message.reply_text(
                f"Delete {label} #{item_id}? Type the ID again to confirm, or Back to cancel.",
                reply_markup=get_back_keyboard(),
            )
            return DELETE_CHOICE

        # Confirmed — perform delete
        context.user_data.pop("delete_confirm", None)
        if delete_type == "event":
            ok = delete_event(update.effective_chat.id, item_id)
        else:
            ok = delete_note(update.effective_chat.id, item_id)

        if ok:
            await update.message.reply_text(
                "✅ Deleted successfully.", reply_markup=get_main_keyboard()
            )
            return ConversationHandler.END
        else:
            await update.message.reply_text(
                "❌ Could not find that ID. Try again or click Back.",
                reply_markup=get_back_keyboard(),
            )
            return DELETE_CHOICE
    except ValueError:
        await update.message.reply_text(
            "Please select an option or enter a valid ID number.",
            reply_markup=get_back_keyboard(),
        )
        return DELETE_CHOICE


# ── Inline Delete callbacks ─────────────────────────────

@restricted
async def inline_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show confirmation inline when user presses [Del] on a list item."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split("|")
    item_type = parts[1]
    item_id = int(parts[2])

    text = f"Delete this {item_type}?"
    markup = build_confirm_delete_inline(item_type, item_id)
    if query.message and query.message.photo:
        await query.edit_message_caption(caption=text, reply_markup=markup)
    else:
        await query.edit_message_text(text=text, reply_markup=markup)


@restricted
async def inline_delete_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute the delete after user confirms."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split("|")
    item_type = parts[1]
    item_id = int(parts[2])
    chat_id = update.effective_chat.id

    if item_type == "event":
        ok = delete_event(chat_id, item_id)
    else:
        ok = delete_note(chat_id, item_id)

    text = "✅ Deleted." if ok else "❌ Could not delete. Item may already be gone."
    if query.message and query.message.photo:
        await query.edit_message_caption(caption=text)
    else:
        await query.edit_message_text(text)


@restricted
async def inline_delete_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel a pending inline delete."""
    query = update.callback_query
    await query.answer()
    if query.message and query.message.photo:
        await query.edit_message_caption(caption="Cancelled.")
    else:
        await query.edit_message_text("Cancelled.")


# ── Inline Edit callbacks ───────────────────────────────

@restricted
async def inline_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry: user pressed [Edit] on a list item. Show field selection."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split("|")
    item_type = parts[1]
    item_id = int(parts[2])
    chat_id = update.effective_chat.id

    context.user_data["inline_edit_type"] = item_type
    context.user_data["inline_edit_id"] = item_id

    if item_type == "event":
        ev = get_event(chat_id, item_id)
        if not ev:
            await query.edit_message_text("Item not found.")
            return ConversationHandler.END
        context.user_data["inline_edit_current"] = {
            "Name": ev["name"],
            "Date": format_display(ev["event_date"]),
            "Time": ev["notify_time"],
            "Recurring": "Yes" if ev.get("recurring") else "No",
            "Reminders": _reminders_display(ev),
        }
        text = f"Edit <b>{secure_text(ev['name'])}</b> — what field?"
    else:
        note = get_note(chat_id, item_id)
        if not note:
            await query.edit_message_text("Item not found.")
            return ConversationHandler.END
        context.user_data["inline_edit_current"] = {
            "Title": note["title"],
            "Content": note["content"],
        }
        text = f"Edit <b>{secure_text(note['title'])}</b> — what field?"

    markup = build_edit_field_inline(item_type, item_id)
    if query.message and query.message.photo:
        await query.edit_message_caption(
            caption=text, parse_mode=ParseMode.HTML, reply_markup=markup
        )
    else:
        await query.edit_message_text(
            text=text, parse_mode=ParseMode.HTML, reply_markup=markup
        )
    return INLINE_AWAIT_FIELD


@restricted
async def inline_field_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User selected a field to edit. Prompt for new value."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split("|")
    field = parts[3]
    context.user_data["inline_edit_field"] = field

    current = context.user_data.get("inline_edit_current", {}).get(field, "Unknown")

    if field == "Recurring":
        text = (
            f"Currently: <b>{current}</b>\n\n"
            "Send <b>Yes</b> for recurring or <b>No</b> for one-time."
        )
    elif field == "Reminders":
        text = (
            f"Currently: <b>{secure_text(str(current))}</b>\n\n"
            "Send comma-separated day offsets, e.g. <code>30,7,1,0</code> "
            "(0 = on the day)."
        )
    elif field == "Content":
        text = (
            f"Currently: <b>{secure_text(str(current))}</b>\n\n"
            "Reply with the new text, or send a photo:"
        )
    else:
        text = (
            f"Currently: <b>{secure_text(str(current))}</b>\n\n"
            "Reply with the new value:"
        )

    if query.message and query.message.photo:
        await query.edit_message_caption(caption=text, parse_mode=ParseMode.HTML)
    else:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML)

    return INLINE_AWAIT_VALUE


@restricted
async def inline_save_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Capture the new value and save the edit."""
    field = context.user_data.get("inline_edit_field")
    item_id = context.user_data.get("inline_edit_id")
    item_type = context.user_data.get("inline_edit_type")
    chat_id = update.effective_chat.id

    if not field or not item_id or item_type not in ("event", "note"):
        await update.message.reply_text(
            "Edit session expired. Please start again.",
            reply_markup=get_main_keyboard(),
        )
        return ConversationHandler.END

    if update.message.photo:
        # Photos are only valid when replacing a note's Content
        if item_type == "note" and field == "Content":
            photo_id = update.message.photo[-1].file_id
            caption = update.message.caption or ""
            ok = update_note(chat_id, item_id, "Content", caption, photo_id=photo_id)
        else:
            await update.message.reply_text(
                f"A photo can't be used for <b>{field}</b>. Send text instead.",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_keyboard(),
            )
            return INLINE_AWAIT_VALUE
    else:
        error, new_value = _normalize_field_value(field, update.message.text or "")
        if error:
            await update.message.reply_text(
                error, parse_mode=ParseMode.HTML, reply_markup=get_back_keyboard()
            )
            return INLINE_AWAIT_VALUE
        if item_type == "event":
            ok = update_event(chat_id, item_id, field, new_value)
        else:
            ok = update_note(chat_id, item_id, field, new_value)

    if not ok:
        await update.message.reply_text(
            "That item no longer exists.", reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"✅ <b>{field}</b> updated.",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard(),
    )
    return ConversationHandler.END


@restricted
async def inline_edit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.message and query.message.photo:
        await query.edit_message_caption(caption="Edit cancelled.")
    else:
        await query.edit_message_text("Edit cancelled.")
    return ConversationHandler.END


# ── Edit (reply-keyboard based) ─────────────────────────

@restricted
async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if "Date" in text:
        context.user_data["edit_type"] = "event"
        events = get_events(update.effective_chat.id)
        if not events:
            await update.message.reply_text(
                "No dates found to edit.", reply_markup=get_main_keyboard()
            )
            return ConversationHandler.END

        msg = "✏️ <b>Reply with the ID to edit:</b>\n\n"
        for ev in events:
            msg += (
                f"ID: <b>{ev['id']}</b> | {secure_text(ev['name'])} "
                f"({format_display(ev['event_date'])})\n"
            )
    else:
        context.user_data["edit_type"] = "note"
        notes = get_notes(update.effective_chat.id)
        if not notes:
            await update.message.reply_text(
                "No notes found to edit.", reply_markup=get_main_keyboard()
            )
            return ConversationHandler.END

        msg = "✏️ <b>Reply with the ID to edit:</b>\n\n"
        for n in notes:
            msg += f"ID: <b>{n['id']}</b> | {secure_text(n['title'])}\n"

    await update.message.reply_text(
        msg, parse_mode=ParseMode.HTML, reply_markup=get_back_keyboard()
    )
    return EDIT_SELECT_ID


async def edit_select_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        item_id = int(update.message.text)
        context.user_data["edit_id"] = item_id
    except ValueError:
        await update.message.reply_text(
            "Please enter a valid number ID.", reply_markup=get_back_keyboard()
        )
        return EDIT_SELECT_ID

    chat_id = update.effective_chat.id

    if context.user_data["edit_type"] == "event":
        ev = get_event(chat_id, item_id)
        if not ev:
            await update.message.reply_text(
                "ID not found. Try again.", reply_markup=get_back_keyboard()
            )
            return EDIT_SELECT_ID

        context.user_data["current_values"] = {
            "Name": ev["name"],
            "Date": format_display(ev["event_date"]),
            "Time": ev["notify_time"],
            "Recurring": "Yes" if ev.get("recurring") else "No",
            "Reminders": _reminders_display(ev),
        }
        await update.message.reply_text(
            f"Found Date: <b>{secure_text(ev['name'])}</b>\nWhat do you want to change?",
            parse_mode=ParseMode.HTML,
            reply_markup=get_event_field_keyboard(),
        )
    else:
        note = get_note(chat_id, item_id)
        if not note:
            await update.message.reply_text(
                "ID not found. Try again.", reply_markup=get_back_keyboard()
            )
            return EDIT_SELECT_ID

        context.user_data["current_values"] = {
            "Title": note["title"],
            "Content": note["content"],
        }
        await update.message.reply_text(
            f"Found Note: <b>{secure_text(note['title'])}</b>\nWhat do you want to change?",
            parse_mode=ParseMode.HTML,
            reply_markup=get_note_field_keyboard(),
        )

    return EDIT_SELECT_FIELD


async def edit_select_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    field = update.message.text
    valid_fields = context.user_data.get("current_values", {})
    if field not in valid_fields:
        await update.message.reply_text(
            "Please choose one of the fields shown below.",
            reply_markup=(
                get_event_field_keyboard()
                if context.user_data.get("edit_type") == "event"
                else get_note_field_keyboard()
            ),
        )
        return EDIT_SELECT_FIELD

    context.user_data["edit_field"] = field

    current_val = valid_fields.get(field, "Unknown")

    if field == "Recurring":
        await update.message.reply_text(
            f"Currently: <b>{current_val}</b>\n\n"
            "Reply <b>Yes</b> for recurring or <b>No</b> for one-time.",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(),
        )
    elif field == "Reminders":
        await update.message.reply_text(
            f"Currently: <b>{secure_text(str(current_val))}</b>\n\n"
            "Send comma-separated day offsets, e.g. <code>30,7,1,0</code> "
            "(0 = on the day).",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(),
        )
    else:
        await update.message.reply_text(
            f"Current <b>{field}</b> is: {secure_text(str(current_val))}\n\n"
            "Please enter the new value:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(),
        )
    return EDIT_NEW_VALUE


async def edit_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    field = context.user_data["edit_field"]
    item_id = context.user_data["edit_id"]
    chat_id = update.effective_chat.id
    is_event = context.user_data["edit_type"] == "event"

    if update.message.photo:
        # Photos are only valid when replacing a note's Content
        if not is_event and field == "Content":
            photo_id = update.message.photo[-1].file_id
            caption = update.message.caption or ""
            if not update_note(chat_id, item_id, "Content", caption, photo_id=photo_id):
                await update.message.reply_text(
                    "That note no longer exists.", reply_markup=get_main_keyboard()
                )
                return ConversationHandler.END
            await update.message.reply_text(
                "✅ Note updated successfully!", reply_markup=get_main_keyboard()
            )
            return ConversationHandler.END
        await update.message.reply_text(
            f"A photo can't be used for <b>{field}</b>. Send text instead.",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_keyboard(),
        )
        return EDIT_NEW_VALUE

    error, new_value = _normalize_field_value(field, update.message.text or "")
    if error:
        await update.message.reply_text(
            error, parse_mode=ParseMode.HTML, reply_markup=get_back_keyboard()
        )
        return EDIT_NEW_VALUE

    if is_event:
        ok = update_event(chat_id, item_id, field, new_value)
    else:
        ok = update_note(chat_id, item_id, field, new_value)

    if not ok:
        await update.message.reply_text(
            "That item no longer exists.", reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"✅ <b>{field}</b> updated successfully!",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard(),
    )
    return ConversationHandler.END


# ── Handler registration ────────────────────────────────

# Buttons with a global (group 1) handler — a conversation fallback ends
# silently and lets the global handler produce the single reply.
PASSTHROUGH_BUTTONS = (
    r"^📅 List Dates$"
    r"|^📝 View Notes$"
    r"|^❤️ Our Journey$"
    r"|^🔍 Upcoming$"
    r"|^❓ Help$"
    r"|^📤 Export$"
)

# Buttons that start their own conversation — a fallback cancels with a notice
# (the new flow can't start in the same update).
ENTRY_BUTTONS = (
    r"^🔙 Back$"
    r"|^➕ Add Date$"
    r"|^➕ Add Note$"
    r"|^✏️ Edit (Date|Note)$"
    r"|^🗑 Delete Item$"
    r"|^🌍 Set Timezone$"
    r"|^⚙️ Journey Event$"
)

MENU_BUTTONS = f"{PASSTHROUGH_BUTTONS}|{ENTRY_BUTTONS}"

TEXT_FILTER = filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTONS)

PASSTHROUGH_COMMANDS = ["start", "help", "upcoming", "export", "days"]
ENTRY_COMMANDS = ["add", "addnote", "delete", "timezone", "journey", "import"]


def conversation_fallbacks():
    """Fallbacks shared by conversations.

    Menu actions with global handlers end the conversation silently (the
    global handler replies once); actions that start a new conversation
    cancel with a notice.
    """
    return [
        CommandHandler("cancel", back_to_menu),
        CommandHandler(PASSTHROUGH_COMMANDS, end_conversation_silently),
        CommandHandler(ENTRY_COMMANDS, cancel_and_retry),
        MessageHandler(filters.Regex(PASSTHROUGH_BUTTONS), end_conversation_silently),
        MessageHandler(filters.Regex(r"^🔙 Back$"), back_to_menu),
        MessageHandler(filters.Regex(ENTRY_BUTTONS), cancel_and_retry),
    ]


def register_handlers(application):
    """Attach all command and conversation handlers to the application."""

    # Inline edit conversation
    application.add_handler(ConversationHandler(
        entry_points=[
            CallbackQueryHandler(inline_edit_start, pattern=r"^edit\|"),
        ],
        states={
            INLINE_AWAIT_FIELD: [
                CallbackQueryHandler(inline_field_select, pattern=r"^field\|"),
            ],
            INLINE_AWAIT_VALUE: [
                MessageHandler((TEXT_FILTER | filters.PHOTO), inline_save_value),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(inline_edit_cancel, pattern=r"^cancel_edit$"),
            *conversation_fallbacks(),
        ],
    ))

    # Timezone
    application.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("timezone", timezone_start),
            MessageHandler(filters.Regex(r"^🌍 Set Timezone$"), timezone_start),
        ],
        states={
            SET_TIMEZONE: [MessageHandler(TEXT_FILTER, save_timezone)],
        },
        fallbacks=conversation_fallbacks(),
    ))

    # Journey event config
    application.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("journey", journey_event_start),
            MessageHandler(filters.Regex(r"^⚙️ Journey Event$"), journey_event_start),
        ],
        states={
            JOURNEY_EVENT_STATE: [MessageHandler(TEXT_FILTER, save_journey_event)],
        },
        fallbacks=conversation_fallbacks(),
    ))

    # Add Event (typed date or inline calendar)
    application.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("add", add_event_start),
            MessageHandler(filters.Regex(r"^➕ Add Date$"), add_event_start),
        ],
        states={
            NAME: [MessageHandler(TEXT_FILTER, get_name)],
            DATE: [
                MessageHandler(TEXT_FILTER, get_date),
                CallbackQueryHandler(calendar_callback, pattern=r"^cal\|"),
            ],
            TIME: [MessageHandler(TEXT_FILTER, get_time)],
            RECURRING: [MessageHandler(TEXT_FILTER, get_recurring)],
        },
        fallbacks=conversation_fallbacks(),
    ))

    # Add Note
    application.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("addnote", add_note_start),
            MessageHandler(filters.Regex(r"^➕ Add Note$"), add_note_start),
        ],
        states={
            NOTE_TITLE: [MessageHandler(TEXT_FILTER, get_note_title)],
            NOTE_CONTENT: [
                MessageHandler((TEXT_FILTER | filters.PHOTO), get_note_content)
            ],
        },
        fallbacks=conversation_fallbacks(),
    ))

    # Delete
    application.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("delete", delete_start),
            MessageHandler(filters.Regex(r"^🗑 Delete Item$"), delete_start),
        ],
        states={DELETE_CHOICE: [MessageHandler(TEXT_FILTER, delete_router)]},
        fallbacks=conversation_fallbacks(),
    ))

    # Edit (reply-keyboard based)
    application.add_handler(ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^✏️ Edit (Date|Note)$"), edit_start),
        ],
        states={
            EDIT_SELECT_ID: [MessageHandler(TEXT_FILTER, edit_select_id)],
            EDIT_SELECT_FIELD: [MessageHandler(TEXT_FILTER, edit_select_field)],
            EDIT_NEW_VALUE: [
                MessageHandler((TEXT_FILTER | filters.PHOTO), edit_save)
            ],
        },
        fallbacks=conversation_fallbacks(),
    ))

    # Import (JSON backup restore)
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler("import", import_start)],
        states={
            IMPORT_FILE: [
                MessageHandler(filters.Document.ALL, import_receive),
                MessageHandler(TEXT_FILTER, import_receive),
            ],
        },
        fallbacks=conversation_fallbacks(),
    ))

    # Commands and menu buttons (group 1). Conversations live in group 0;
    # their fallbacks end silently for these actions so only one reply is sent.
    application.add_handler(CommandHandler("start", start), group=1)
    application.add_handler(CommandHandler("help", help_command), group=1)
    application.add_handler(CommandHandler("upcoming", upcoming), group=1)
    application.add_handler(CommandHandler("export", export_data), group=1)
    application.add_handler(CommandHandler("days", days_command), group=1)
    application.add_handler(MessageHandler(filters.Regex("^❓ Help$"), help_command), group=1)
    application.add_handler(MessageHandler(filters.Regex("^📅 List Dates$"), list_events), group=1)
    application.add_handler(MessageHandler(filters.Regex("^📝 View Notes$"), list_notes), group=1)
    application.add_handler(MessageHandler(filters.Regex("^❤️ Our Journey$"), our_journey), group=1)
    application.add_handler(MessageHandler(filters.Regex("^🔍 Upcoming$"), upcoming), group=1)
    application.add_handler(MessageHandler(filters.Regex("^📤 Export$"), export_data), group=1)

    # Inline callbacks outside conversations
    application.add_handler(CallbackQueryHandler(inline_delete_confirm, pattern=r"^del\|"), group=1)
    application.add_handler(CallbackQueryHandler(inline_delete_execute, pattern=r"^confirm_del\|"), group=1)
    application.add_handler(CallbackQueryHandler(inline_delete_cancel, pattern=r"^cancel_del$"), group=1)
    application.add_handler(CallbackQueryHandler(snooze_reminder, pattern=r"^snz\|"), group=1)
    application.add_handler(CallbackQueryHandler(page_events, pattern=r"^pg\|ev\|"), group=1)
    application.add_handler(CallbackQueryHandler(page_notes, pattern=r"^pg\|nt\|"), group=1)
    application.add_handler(CallbackQueryHandler(
        lambda u, c: u.callback_query.answer(), pattern=r"^pg_noop$"
    ), group=1)

    application.add_error_handler(error_handler)
