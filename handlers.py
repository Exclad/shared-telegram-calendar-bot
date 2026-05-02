"""Conversation handlers and command handlers for the bot."""

import calendar
import html
import logging
from datetime import datetime
from functools import wraps

import pytz
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters,
)
from telegram.error import NetworkError, Forbidden, Conflict

from config import (
    ALLOWED_USERS,
    NAME,
    DATE,
    TIME,
    NOTE_TITLE,
    NOTE_CONTENT,
    DELETE_CHOICE,
    SET_TIMEZONE,
    EDIT_SELECT_ID,
    EDIT_SELECT_FIELD,
    EDIT_NEW_VALUE,
)
from db import (
    add_event,
    get_events,
    get_event,
    update_event,
    delete_event,
    get_anniversary_date,
    add_note,
    get_notes,
    get_note,
    update_note,
    delete_note,
    get_timezone,
    set_timezone as db_set_timezone,
)
from keyboard import (
    get_main_keyboard,
    get_back_keyboard,
    get_timezone_keyboard,
    get_delete_choice_keyboard,
    get_event_field_keyboard,
    get_note_field_keyboard,
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


async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "\U0001f519 Returned to Main Menu.", reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END


# ── Our Journey ─────────────────────────────────────────

@restricted
async def our_journey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date_str = get_anniversary_date(update.effective_chat.id)

    if not date_str:
        await update.message.reply_text(
            "\U0001f494 I don't know when you started!\n\n"
            "Please add an event named **Anniversary** so I can calculate your time together.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    try:
        start_date = datetime.strptime(date_str, "%d-%m-%Y")
        today = datetime.now()
        years, months, days = calculate_elapsed(start_date, today)
        total_days = (today - start_date).days

        msg = (
            f"❤️ **Our Journey Together** ❤️\n\n"
            f"We have been together for:\n"
            f"**{years}** Years, **{months}** Months, and **{days}** Days.\n\n"
            f"That is **{total_days}** days of love! \U0001f618"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await update.message.reply_text(
            "Error calculating date. Please check your Anniversary date format."
        )


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

    if user_tz not in pytz.all_timezones:
        await update.message.reply_text(
            "❌ Invalid Timezone.\n"
            "Please choose from the buttons or check spelling (Case Sensitive, e.g., 'Asia/Singapore').",
            reply_markup=get_back_keyboard(),
        )
        return SET_TIMEZONE

    db_set_timezone(update.effective_chat.id, user_tz)

    await update.message.reply_text(
        f"✅ Timezone set to **{user_tz}**.",
        parse_mode=ParseMode.MARKDOWN,
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
            "Date saved! Now, what time to remind? (Format: HH:MM)\nType 'skip' for 12:00 ParseMode."
        )
        return TIME
    except ValueError:
        await update.message.reply_text("Invalid format. Please use DD-MM-YYYY.")
        return DATE


async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    time_text = update.message.text.strip()
    if time_text.lower() == "skip":
        final_time = "12:00"
    else:
        try:
            datetime.strptime(time_text, "%H:%M")
            final_time = time_text
        except ValueError:
            await update.message.reply_text("Invalid format. Use HH:MM or type 'skip'.")
            return TIME

    add_event(
        update.effective_chat.id,
        context.user_data["event_name"],
        context.user_data["event_date"],
        final_time,
    )

    await update.message.reply_text(
        f"✅ Saved: <b>{secure_text(context.user_data['event_name'])}</b> on {context.user_data['event_date']}!",
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

@restricted
async def list_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    events = get_events(update.effective_chat.id)
    user_tz = get_timezone(update.effective_chat.id)

    if not events:
        await update.message.reply_text(
            "No dates saved yet!", reply_markup=get_main_keyboard()
        )
        return

    message = (
        f"\U0001f4c5 <b>Your Important Dates:</b>\n"
        f"(Timezone: {user_tz})\n\n"
    )
    for ev in events:
        message += (
            f"• ID:{ev['id']} | <b>{secure_text(ev['name'])}</b>: "
            f"{ev['event_date']} (Alert: {ev['notify_time']})\n"
        )

    await update.message.reply_text(
        message, parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard()
    )


# ── List Notes ──────────────────────────────────────────

@restricted
async def list_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    notes = get_notes(update.effective_chat.id)

    if not notes:
        await update.message.reply_text(
            "No notes saved yet!", reply_markup=get_main_keyboard()
        )
        return

    text_notes_message = "\U0001f4dd <b>Your Saved Notes:</b>\n\n"
    image_notes = []
    has_text_notes = False

    for note in notes:
        if note["photo_id"]:
            image_notes.append(note)
        else:
            has_text_notes = True
            text_notes_message += f"\U0001f4cc ID:{note['id']} | <b>{secure_text(note['title'])}</b>\n"
            if note["content"]:
                text_notes_message += f"<code>{secure_text(note['content'])}</code>\n\n"

    if has_text_notes:
        await update.message.reply_text(text_notes_message, parse_mode=ParseMode.HTML)
    elif not image_notes:
        await update.message.reply_text("No text notes found.")

    for note in image_notes:
        caption = f"\U0001f4cc ID:{note['id']} | <b>{secure_text(note['title'])}</b>"
        if note["content"]:
            caption += f"\n{secure_text(note['content'])}"
        await update.message.reply_photo(
            photo=note["photo_id"], caption=caption, parse_mode=ParseMode.HTML
        )

    await update.message.reply_text("Done.", reply_markup=get_main_keyboard())


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

    # Try parsing as an ID number
    try:
        item_id = int(text)
        delete_type = context.user_data.get("delete_type")

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


# ── Edit ────────────────────────────────────────────────

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
        if field == "Name":
            pass  # no format validation needed
        elif field == "Date":
            try:
                datetime.strptime(new_value, "%d-%m-%Y")
            except ValueError:
                await update.message.reply_text(
                    "Invalid Date Format. Use DD-MM-YYYY.",
                    reply_markup=get_back_keyboard(),
                )
                return EDIT_NEW_VALUE
        elif field == "Time":
            try:
                datetime.strptime(new_value, "%H:%M")
            except ValueError:
                await update.message.reply_text(
                    "Invalid Time Format. Use HH:MM.",
                    reply_markup=get_back_keyboard(),
                )
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


# ── Handler registrars ──────────────────────────────────

TEXT_FILTER = filters.TEXT & ~filters.COMMAND & ~filters.Regex("^\U0001f519 Back$")


def register_handlers(application):
    """Attach all command and conversation handlers to the application."""

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Regex("^\U0001f4c5 List Dates$"), list_events))
    application.add_handler(MessageHandler(filters.Regex("^\U0001f4dd View Notes$"), list_notes))
    application.add_handler(MessageHandler(filters.Regex("^❤️ Our Journey$"), our_journey))

    # Timezone
    application.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("timezone", timezone_start),
            MessageHandler(filters.Regex("^\U0001f30d Set Timezone$"), timezone_start),
        ],
        states={
            SET_TIMEZONE: [MessageHandler(TEXT_FILTER, save_timezone)],
        },
        fallbacks=[
            CommandHandler("cancel", back_to_menu),
            MessageHandler(filters.Regex("^\U0001f519 Back$"), back_to_menu),
        ],
    ))

    # Add Event
    application.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("add", add_event_start),
            MessageHandler(filters.Regex("^➕ Add Date$"), add_event_start),
        ],
        states={
            NAME: [MessageHandler(TEXT_FILTER, get_name)],
            DATE: [MessageHandler(TEXT_FILTER, get_date)],
            TIME: [MessageHandler(TEXT_FILTER, get_time)],
        },
        fallbacks=[
            CommandHandler("cancel", back_to_menu),
            MessageHandler(filters.Regex("^\U0001f519 Back$"), back_to_menu),
        ],
    ))

    # Add Note
    application.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("addnote", add_note_start),
            MessageHandler(filters.Regex("^➕ Add Note$"), add_note_start),
        ],
        states={
            NOTE_TITLE: [MessageHandler(TEXT_FILTER, get_note_title)],
            NOTE_CONTENT: [
                MessageHandler((TEXT_FILTER | filters.PHOTO), get_note_content)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", back_to_menu),
            MessageHandler(filters.Regex("^\U0001f519 Back$"), back_to_menu),
        ],
    ))

    # Delete
    application.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("delete", delete_start),
            MessageHandler(filters.Regex("^\U0001f5d1 Delete Item$"), delete_start),
        ],
        states={DELETE_CHOICE: [MessageHandler(TEXT_FILTER, delete_router)]},
        fallbacks=[
            CommandHandler("cancel", back_to_menu),
            MessageHandler(filters.Regex("^\U0001f519 Back$"), back_to_menu),
        ],
    ))

    # Edit
    application.add_handler(ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^✏️ Edit (Date|Note)$"), edit_start),
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
            MessageHandler(filters.Regex("^\U0001f519 Back$"), back_to_menu),
        ],
    ))

    application.add_error_handler(error_handler)
