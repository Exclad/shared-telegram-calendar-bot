"""Conversation handlers and command handlers for the bot."""

import calendar
import html
import logging
from datetime import datetime, timedelta, timezone as dt_timezone
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
    INLINE_AWAIT_FIELD,
    INLINE_AWAIT_VALUE,
    JOURNEY_EVENT_STATE,
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
    build_event_list_inline,
    build_note_list_inline,
    build_confirm_delete_inline,
    build_edit_field_inline,
    build_pagination_nav,
)

logger = logging.getLogger(__name__)


def secure_text(value: str) -> str:
    """Escape HTML characters in user-provided text."""
    return html.escape(str(value)) if value else ""


def restricted(func):
    """Decorator: only allow users in ALLOWED_USERS list."""

    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in ALLOWED_USERS:
            logger.warning("Unauthorized access attempt from user %s", user_id)
            if update.message:
                await update.message.reply_text("⛔️ Sorry, this is a private bot.")
            elif update.callback_query:
                await update.callback_query.answer("⛔️ Sorry, this is a private bot.", show_alert=True)
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
    """Accurate years/months/days calculation respecting variable month lengths."""
    years = today.year - start_date.year
    months = today.month - start_date.month
    days = today.day - start_date.day

    if days < 0:
        months -= 1
        prev_month = today.month - 1 if today.month > 1 else 12
        prev_year = today.year if today.month > 1 else today.year - 1
        days += calendar.monthrange(prev_year, prev_month)[1]

    if months < 0:
        years -= 1
        months += 12

    return years, months, days


PER_PAGE = 5


def _validate_field_value(field: str, value: str) -> str | None:
    """Validate a field value for edit/create. Returns error message or None."""
    if field == "Date":
        try:
            datetime.strptime(value, "%d-%m-%Y")
        except ValueError:
            return "Invalid Date Format. Use DD-MM-YYYY."
    elif field == "Time":
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError:
            return "Invalid Time Format. Use HH:MM."
    return None


def _is_passed_one_time(ev: dict) -> bool:
    """Check if a one-time event has already passed (uses UTC for consistency)."""
    if ev.get("recurring"):
        return False
    try:
        ev_date = datetime.strptime(ev["event_date"], "%d-%m-%Y").date()
        return ev_date < datetime.now(dt_timezone.utc).date()
    except ValueError:
        return False


# ── Basic commands ──────────────────────────────────────

@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "\U0001f44b **Hello!**\n\n"
        "I am ready to track your important memories.\n"
        "Use the buttons below to control me.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_keyboard(),
    )


@restricted
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "❓ **Help & Commands**\n\n"
        "**Menu buttons:**\n"
        "➕ Add Date — save a birthday, anniversary, etc.\n"
        "📅 List Dates — view saved dates (with edit/delete buttons)\n"
        "➕ Add Note — save text or a photo\n"
        "📝 View Notes — view saved notes (with edit/delete buttons)\n"
        "✏️ Edit Date/Note — edit an item by its ID\n"
        "🗑 Delete Item — delete an item by its ID\n"
        "❤️ Our Journey — show time since your anniversary\n"
        "🔍 Upcoming — events in the next 3 months\n"
        "🌍 Set Timezone — set your timezone for accurate alerts\n"
        "⚙️ Journey Event — change which event powers Our Journey\n"
        "📤 Export — dump all dates and notes as text\n\n"
        "**Commands:**\n"
        "/start — show main menu\n"
        "/help — show this message\n"
        "/add — add a new date\n"
        "/addnote — add a new note\n"
        "/upcoming — upcoming events\n"
        "/export — export all data\n"
        "/timezone — set timezone\n"
        "/journey — change journey event\n"
        "/delete — delete an item\n"
        "/cancel — cancel current operation"
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_keyboard()
    )


async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "\U0001f519 Returned to Main Menu.", reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END


# ── Our Journey ─────────────────────────────────────────

@restricted
async def our_journey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    date_str, event_name = get_journey_event_for_chat(chat_id)

    if not date_str:
        await update.message.reply_text(
            "\U0001f494 I don't know when you started!\n\n"
            f"Please add an event named <b>{secure_text(event_name)}</b> so I can calculate your time together.\n\n"
            "You can change which event to use with ⚙️ Journey Event.",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        start_date = datetime.strptime(date_str, "%d-%m-%Y").replace(tzinfo=dt_timezone.utc)
        today = datetime.now(dt_timezone.utc)
        years, months, days = calculate_elapsed(start_date, today)
        total_days = (today - start_date).days

        msg = (
            f"❤️ <b>Our Journey Together</b> ❤️\n\n"
            f"Since <b>{date_str}</b>\n"
            f"We have been together for:\n"
            f"<b>{years}</b> Years, <b>{months}</b> Months, and <b>{days}</b> Days.\n\n"
            f"That is <b>{total_days}</b> days of love! \U0001f618"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    except ValueError:
        await update.message.reply_text(
            "Error calculating date. Please check your date format."
        )


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

    set_journey_event(chat_id, match["name"])

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
        "\U0001f30d **Select your Timezone**\n\n"
        "This ensures you get alerts at the correct time.\n"
        "Choose a button or type your timezone (e.g., 'Asia/Tokyo').",
        parse_mode=ParseMode.MARKDOWN,
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
    context.user_data["event_name"] = update.message.text.strip()
    await update.message.reply_text(
        "Great! What is the date? Format: DD-MM-YYYY (e.g., 17-09-2022)"
    )
    return DATE


async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date_text = update.message.text.strip()
    try:
        datetime.strptime(date_text, "%d-%m-%Y")
        context.user_data["event_date"] = date_text
        await update.message.reply_text(
            "Date saved! Now, what time to remind? (Format: HH:MM)\nType 'skip' for 12:00 PM."
        )
        return TIME
    except ValueError:
        await update.message.reply_text("Invalid format. Please use DD-MM-YYYY.")
        return DATE


async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    time_text = update.message.text.strip()
    if time_text.lower() == "skip":
        context.user_data["notify_time"] = "12:00"
    else:
        try:
            datetime.strptime(time_text, "%H:%M")
            context.user_data["notify_time"] = time_text
        except ValueError:
            await update.message.reply_text("Invalid format. Use HH:MM or type 'skip'.")
            return TIME

    await update.message.reply_text(
        "Is this a recurring event (yearly) or one-time?",
        reply_markup=get_recurring_keyboard(),
    )
    return RECURRING


async def get_recurring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    recurring = text.startswith("yes")

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
        f"on {context.user_data['event_date']} ({label})!",
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
    context.user_data["note_title"] = update.message.text.strip()
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
    # Filter passed one-time events
    active = [e for e in all_events if not _is_passed_one_time(e)]
    total = max(1, (len(active) + PER_PAGE - 1) // PER_PAGE)
    page = min(page, total)
    start = (page - 1) * PER_PAGE
    page_events = active[start:start + PER_PAGE]

    msg = (
        f"\U0001f4c5 <b>Your Important Dates:</b>\n"
        f"(Timezone: {user_tz}) Page {page}/{total}\n\n"
    )
    for ev in page_events:
        label = "♻️" if ev.get("recurring") else "1️⃣"
        msg += (
            f"{label} <b>{secure_text(ev['name'])}</b>: "
            f"{ev['event_date']} at {ev['notify_time']}\n"
        )
    if len(all_events) > len(active):
        msg += "\n_(Passed one-time events hidden)_"
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
    image_notes = [n for n in all_notes if n["photo_id"]]

    if not all_notes:
        await update.message.reply_text(
            "No notes saved yet!", reply_markup=get_main_keyboard()
        )
        return

    # Text notes — paginated
    msg, total, page_notes = _build_note_page(chat_id, page)
    inline_kb = build_note_list_inline(page_notes)
    nav_rows = build_pagination_nav(page, total, "nt")
    keyboard = InlineKeyboardMarkup(
        list(inline_kb.inline_keyboard if inline_kb else []) + nav_rows
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    # Photo notes — shown non-paginated at the end
    for note in image_notes:
        caption = f"\U0001f4cc <b>{secure_text(note['title'])}</b>"
        if note["content"]:
            caption += f"\n{secure_text(note['content'])}"
        await update.message.reply_photo(
            photo=note["photo_id"], caption=caption, parse_mode=ParseMode.HTML
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


# ── Upcoming ────────────────────────────────────────────

@restricted
async def upcoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    events = get_events(update.effective_chat.id)
    user_tz = get_timezone(update.effective_chat.id)

    try:
        tz = ZoneInfo(user_tz)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")

    today = datetime.now(tz).date()
    cutoff = today + timedelta(days=90)

    upcoming_list: list[tuple[str, str, int]] = []  # (name, date_str, days_until)

    for ev in events:
        try:
            ev_date = datetime.strptime(ev["event_date"], "%d-%m-%Y").date()
            if not ev.get("recurring") and ev_date < today:
                continue  # one-time event already passed
            this_year = ev_date.replace(year=today.year)
            if this_year < today:
                this_year = ev_date.replace(year=today.year + 1)
            days_until = (this_year - today).days
            if days_until <= 90:
                upcoming_list.append((ev["name"], this_year.strftime("%d-%m-%Y"), days_until))
        except ValueError:
            continue

    upcoming_list.sort(key=lambda x: x[2])

    if not upcoming_list:
        await update.message.reply_text(
            "No events in the next 3 months.",
            reply_markup=get_main_keyboard(),
        )
        return

    msg = f"🔍 <b>Upcoming Events</b> (next 3 months, {user_tz})\n\n"
    for name, date_str, days in upcoming_list:
        when = f"in {days} day(s)" if days > 0 else "TODAY!"
        msg += f"• <b>{secure_text(name)}</b> — {date_str} ({when})\n"

    await update.message.reply_text(
        msg, parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard()
    )


# ── Export ──────────────────────────────────────────────

@restricted
async def export_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    events = get_events(update.effective_chat.id)
    notes = get_notes(update.effective_chat.id)
    user_tz = get_timezone(update.effective_chat.id)

    msg = "📤 **Your Exported Data**\n\n"
    msg += f"Timezone: {user_tz}\n\n"

    msg += "─── Dates ───\n"
    if events:
        for ev in events:
            label = "♻️" if ev.get("recurring") else "1️⃣"
            msg += f"{label} {secure_text(ev['name'])}: {ev['event_date']} at {ev['notify_time']}\n"
    else:
        msg += "(none)\n"

    msg += "\n─── Notes ───\n"
    if notes:
        for n in notes:
            msg += f"📌 {secure_text(n['title'])}"
            if n["content"]:
                msg += f": {secure_text(n['content'])}"
            if n["photo_id"]:
                msg += " [photo — view in Telegram to see]"
            msg += "\n"
    else:
        msg += "(none)\n"

    await update.message.reply_text(
        msg, parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard()
    )


# ── Delete ──────────────────────────────────────────────

@restricted
async def delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "What would you like to delete?",
        reply_markup=get_delete_choice_keyboard(),
    )
    return DELETE_CHOICE


async def delete_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "Delete Date":
        context.user_data["delete_type"] = "event"
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
                f"({ev['event_date']})\n"
            )
        await update.message.reply_text(
            msg, parse_mode=ParseMode.HTML, reply_markup=get_back_keyboard()
        )
        return DELETE_CHOICE

    elif text == "Delete Note":
        context.user_data["delete_type"] = "note"
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

        # Confirmation step: if not yet confirmed, ask
        if context.user_data.get("delete_confirm_id") != item_id:
            context.user_data["delete_confirm_id"] = item_id
            label = "date" if delete_type == "event" else "note"
            await update.message.reply_text(
                f"Delete {label} #{item_id}? Type the ID again to confirm, or Back to cancel.",
                reply_markup=get_back_keyboard(),
            )
            return DELETE_CHOICE

        # Confirmed — perform delete
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

    await query.edit_message_text(
        text=f"Delete this {item_type}?",
        reply_markup=build_confirm_delete_inline(item_type, item_id),
    )


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

    if ok:
        await query.edit_message_text("✅ Deleted.")
    else:
        await query.edit_message_text("❌ Could not delete. Item may already be gone.")


@restricted
async def inline_delete_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel a pending inline delete."""
    query = update.callback_query
    await query.answer()
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
            "Date": ev["event_date"],
            "Time": ev["notify_time"],
            "Recurring": "Yes" if ev.get("recurring") else "No",
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

    await query.edit_message_text(
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=build_edit_field_inline(item_type, item_id),
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
        await query.edit_message_text(
            f"Currently: <b>{current}</b>\n\n"
            "Send <b>Yes</b> for recurring or <b>No</b> for one-time.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await query.edit_message_text(
            f"Currently: <b>{secure_text(str(current))}</b>\n\n"
            "Reply with the new value, or send a photo if editing content:",
            parse_mode=ParseMode.HTML,
        )

    return INLINE_AWAIT_VALUE


@restricted
async def inline_save_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Capture the new value and save the edit."""
    field = context.user_data["inline_edit_field"]
    item_id = context.user_data["inline_edit_id"]
    item_type = context.user_data["inline_edit_type"]
    chat_id = update.effective_chat.id

    if update.message.photo and item_type == "note" and field == "Content":
        photo_id = update.message.photo[-1].file_id
        caption = update.message.caption or ""
        update_note(chat_id, item_id, "Content", caption, photo_id=photo_id)
    else:
        new_value = update.message.text or ""
        if item_type == "event":
            error = _validate_field_value(field, new_value)
            if error:
                await update.message.reply_text(error, reply_markup=get_back_keyboard())
                return INLINE_AWAIT_VALUE
            update_event(chat_id, item_id, field, new_value)
        else:
            update_note(chat_id, item_id, field, new_value)

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
                f"({ev['event_date']})\n"
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
            "Date": ev["event_date"],
            "Time": ev["notify_time"],
            "Recurring": "Yes" if ev.get("recurring") else "No",
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
    context.user_data["edit_field"] = field

    current_val = context.user_data["current_values"].get(field, "Unknown")

    if field == "Recurring":
        await update.message.reply_text(
            f"Currently: <b>{current_val}</b>\n\n"
            "Reply <b>Yes</b> for recurring or <b>No</b> for one-time.",
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
    new_value = update.message.text or ""
    field = context.user_data["edit_field"]
    item_id = context.user_data["edit_id"]
    chat_id = update.effective_chat.id

    if context.user_data["edit_type"] == "event":
        error = _validate_field_value(field, new_value)
        if error:
            await update.message.reply_text(error, reply_markup=get_back_keyboard())
            return EDIT_NEW_VALUE
        update_event(chat_id, item_id, field, new_value)
    else:  # note
        if field == "Content" and update.message.photo:
            photo_id = update.message.photo[-1].file_id
            caption = update.message.caption or ""
            update_note(chat_id, item_id, "Content", caption, photo_id=photo_id)
            await update.message.reply_text(
                "✅ Note updated successfully!", reply_markup=get_main_keyboard()
            )
            return ConversationHandler.END
        update_note(chat_id, item_id, field, new_value)

    await update.message.reply_text(
        f"✅ <b>{field}</b> updated successfully!",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard(),
    )
    return ConversationHandler.END


# ── Handler registration ────────────────────────────────

MENU_BUTTONS = (
    r"^🔙 Back$"
    r"|^📅 List Dates$"
    r"|^📝 View Notes$"
    r"|^❤️ Our Journey$"
    r"|^🌍 Set Timezone$"
    r"|^➕ Add Date$"
    r"|^➕ Add Note$"
    r"|^✏️ Edit (Date|Note)$"
    r"|^🗑 Delete Item$"
    r"|^🔍 Upcoming$"
    r"|^❓ Help$"
    r"|^📤 Export$"
    r"|^⚙️ Journey Event$"
    r"|^Yes \(Recurring\)$"
    r"|^No \(One-time\)$"
    r"|^Delete Date$"
    r"|^Delete Note$"
)

TEXT_FILTER = filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTONS)


def register_handlers(application):
    """Attach all command and conversation handlers to the application."""

    # Commands and menu buttons
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("upcoming", upcoming))
    application.add_handler(CommandHandler("export", export_data))
    application.add_handler(MessageHandler(filters.Regex("^❓ Help$"), help_command))
    application.add_handler(MessageHandler(filters.Regex("^📅 List Dates$"), list_events))
    application.add_handler(MessageHandler(filters.Regex("^📝 View Notes$"), list_notes))
    application.add_handler(MessageHandler(filters.Regex("^❤️ Our Journey$"), our_journey))
    application.add_handler(MessageHandler(filters.Regex("^🔍 Upcoming$"), upcoming))
    application.add_handler(MessageHandler(filters.Regex("^📤 Export$"), export_data))

    # Inline delete callbacks (standalone, not in a conversation)
    application.add_handler(CallbackQueryHandler(inline_delete_confirm, pattern=r"^del\|"))
    application.add_handler(CallbackQueryHandler(inline_delete_execute, pattern=r"^confirm_del\|"))
    application.add_handler(CallbackQueryHandler(inline_delete_cancel, pattern=r"^cancel_del$"))

    # Pagination callbacks
    application.add_handler(CallbackQueryHandler(page_events, pattern=r"^pg\|ev\|"))
    application.add_handler(CallbackQueryHandler(page_notes, pattern=r"^pg\|nt\|"))
    application.add_handler(CallbackQueryHandler(
        lambda u, c: u.callback_query.answer(), pattern=r"^pg_noop$"
    ))

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
            CommandHandler("cancel", back_to_menu),
            MessageHandler(filters.Regex(r"^🔙 Back$"), back_to_menu),
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
        fallbacks=[
            CommandHandler("cancel", back_to_menu),
            MessageHandler(filters.Regex(r"^🔙 Back$"), back_to_menu),
        ],
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
        fallbacks=[
            CommandHandler("cancel", back_to_menu),
            MessageHandler(filters.Regex(r"^🔙 Back$"), back_to_menu),
        ],
    ))

    # Add Event
    application.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("add", add_event_start),
            MessageHandler(filters.Regex(r"^➕ Add Date$"), add_event_start),
        ],
        states={
            NAME: [MessageHandler(TEXT_FILTER, get_name)],
            DATE: [MessageHandler(TEXT_FILTER, get_date)],
            TIME: [MessageHandler(TEXT_FILTER, get_time)],
            RECURRING: [MessageHandler(TEXT_FILTER, get_recurring)],
        },
        fallbacks=[
            CommandHandler("cancel", back_to_menu),
            MessageHandler(filters.Regex(r"^🔙 Back$"), back_to_menu),
        ],
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
        fallbacks=[
            CommandHandler("cancel", back_to_menu),
            MessageHandler(filters.Regex(r"^🔙 Back$"), back_to_menu),
        ],
    ))

    # Delete
    application.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("delete", delete_start),
            MessageHandler(filters.Regex(r"^🗑 Delete Item$"), delete_start),
        ],
        states={DELETE_CHOICE: [MessageHandler(TEXT_FILTER, delete_router)]},
        fallbacks=[
            CommandHandler("cancel", back_to_menu),
            MessageHandler(filters.Regex(r"^🔙 Back$"), back_to_menu),
        ],
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
        fallbacks=[
            CommandHandler("cancel", back_to_menu),
            MessageHandler(filters.Regex(r"^🔙 Back$"), back_to_menu),
        ],
    ))

    application.add_error_handler(error_handler)
