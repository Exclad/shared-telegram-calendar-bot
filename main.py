import logging
import sqlite3
import os
import html
import pytz  # Used for Timezone calculations
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.error import NetworkError, Forbidden

# --- CONFIGURATION ---
# Load environment variables from the .env file (where your Token lives)
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")

# Safety check: Stop the bot if no token is found
if not TOKEN:
    print("Error: TELEGRAM_TOKEN not found in .env file.")
    exit()

# --- LOGGING SETUP ---
# 1. Basic Logging: This prints info to your console so you know what the bot is doing.
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# 2. Silence Noisy Logs: Libraries like 'httpx' and 'apscheduler' print too much info.
# We set them to WARNING so they only print if something actually goes wrong.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

# --- DATABASE SETUP ---
def init_db():
    """
    Creates the necessary database tables if they don't exist yet.
    Run this once when the bot starts.
    """
    conn = sqlite3.connect("dates.db")
    cursor = conn.cursor()
    
    # Table for storing dates/events (Anniversaries, Birthdays)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            name TEXT,
            event_date TEXT,
            notify_time TEXT
        )
    """)
    
    # Table for storing text notes and photos
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            title TEXT,
            content TEXT,
            photo_id TEXT
        )
    """)
    
    # Table for storing the user's preferred Timezone (New Feature)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            chat_id INTEGER PRIMARY KEY,
            timezone TEXT
        )
    """)
    
    conn.commit()
    conn.close()

# --- CONVERSATION STATES ---
# These numbers represent "steps" in the conversation flows.
# e.g., Step 0 is asking for a Name, Step 1 is asking for a Date.
NAME, DATE, TIME = range(3)
NOTE_TITLE, NOTE_CONTENT = range(3, 5)
DELETE_CHOICE = 5
SET_TIMEZONE = 6 

# --- HELPER FUNCTIONS: KEYBOARDS ---

def get_main_keyboard():
    """Returns the main menu buttons seen at the bottom of the chat."""
    keyboard = [
        [KeyboardButton("📅 List Dates"), KeyboardButton("➕ Add Date")],
        [KeyboardButton("📝 View Notes"), KeyboardButton("➕ Add Note")],
        [KeyboardButton("❤️ Our Journey"), KeyboardButton("🗑 Delete Item")],
        [KeyboardButton("🌍 Set Timezone")]  # The new button for Timezones
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_back_keyboard():
    """Returns a simple 'Back' button to cancel actions."""
    return ReplyKeyboardMarkup([[KeyboardButton("🔙 Back")]], resize_keyboard=True)

def secure_text(text):
    """
    Security helper: Escapes HTML characters (<, >, &) in user text.
    This prevents code injection or formatting errors in Telegram messages.
    """
    if text:
        return html.escape(text)
    return ""

# --- GENERIC COMMANDS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggered by /start. Welcomes the user and shows the menu."""
    await update.message.reply_text(
        "👋 **Hello!**\n\n"
        "I am ready to track your important memories.\n"
        "Use the buttons below to control me.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_keyboard()
    )

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggered if the user clicks 'Back'. Cancels the current conversation."""
    await update.message.reply_text("🔙 Returned to Main Menu.", reply_markup=get_main_keyboard())
    return ConversationHandler.END

# --- OUR JOURNEY LOGIC ---

async def our_journey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Calculates how long you have been together based on an event named 'Anniversary'.
    """
    conn = sqlite3.connect("dates.db")
    cursor = conn.cursor()
    # Find the event that starts with "Anniversary"
    cursor.execute("SELECT event_date FROM events WHERE chat_id = ? AND name LIKE 'Anniversary%' LIMIT 1", (update.effective_chat.id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        await update.message.reply_text(
            "💔 I don't know when you started!\n\n"
            "Please add an event named **Anniversary** so I can calculate your time together.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        # Math to calculate years, months, and days
        start_date = datetime.strptime(row[0], "%d-%m-%Y").date()
        today = datetime.now().date()
        
        delta = today - start_date
        total_days = delta.days
        
        years = total_days // 365
        remaining_days = total_days % 365
        months = remaining_days // 30
        days = remaining_days % 30

        msg = (
            f"❤️ **Our Journey Together** ❤️\n\n"
            f"We have been together for:\n"
            f"**{years}** Years, **{months}** Months, and **{days}** Days.\n\n"
            f"That is **{total_days}** days of love! 😘"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    except ValueError:
        await update.message.reply_text("Error calculating date. Please check your Anniversary date format.")

# --- TIMEZONE LOGIC ---

async def timezone_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 1: Ask the user to pick a timezone."""
    keyboard = [
        [KeyboardButton("Asia/Singapore"), KeyboardButton("UTC")],
        [KeyboardButton("US/Eastern"), KeyboardButton("Europe/London")],
        [KeyboardButton("🔙 Back")]
    ]
    await update.message.reply_text(
        "🌍 **Select your Timezone**\n\n"
        "This ensures you get alerts at the correct time.\n"
        "Choose a button or type your timezone (e.g., 'Asia/Tokyo').",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return SET_TIMEZONE

async def save_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 2: Save the selected timezone to the database."""
    user_tz = update.message.text.strip()
    
    # Check if the timezone string is valid in Python's pytz library
    if user_tz not in pytz.all_timezones:
        await update.message.reply_text(
            "❌ Invalid Timezone.\n"
            "Please choose from the buttons or check spelling (Case Sensitive, e.g., 'Asia/Singapore').",
            reply_markup=get_back_keyboard()
        )
        return SET_TIMEZONE

    conn = sqlite3.connect("dates.db")
    cursor = conn.cursor()
    # REPLACE works like "Insert if new, Update if exists"
    cursor.execute("REPLACE INTO user_settings (chat_id, timezone) VALUES (?, ?)", 
                   (update.effective_chat.id, user_tz))
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ Timezone set to **{user_tz}**.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END

# --- ADD EVENT LOGIC ---

async def add_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 1: Ask for the event name."""
    await update.message.reply_text(
        "What is the name of the event? (e.g., Anniversary)", 
        reply_markup=get_back_keyboard()
    )
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 2: Save name, Ask for date."""
    context.user_data['event_name'] = update.message.text
    await update.message.reply_text("Great! What is the date? Format: DD-MM-YYYY (e.g., 17-09-2022)")
    return DATE

async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 3: Save date, Ask for notification time."""
    date_text = update.message.text
    try:
        # Validate date format
        datetime.strptime(date_text, "%d-%m-%Y") 
        context.user_data['event_date'] = date_text
        await update.message.reply_text("Date saved! Now, what time to remind? (Format: HH:MM)\nType 'skip' for 12:00 PM.")
        return TIME
    except ValueError:
        await update.message.reply_text("Invalid format. Please use DD-MM-YYYY.")
        return DATE

async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 4: Save time and write everything to Database."""
    time_text = update.message.text.strip()
    if time_text.lower() == 'skip':
        final_time = "12:00"
    else:
        try:
            datetime.strptime(time_text, "%H:%M")
            final_time = time_text
        except ValueError:
            await update.message.reply_text("Invalid format. Use HH:MM or type 'skip'.")
            return TIME

    conn = sqlite3.connect("dates.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO events (chat_id, name, event_date, notify_time) VALUES (?, ?, ?, ?)",
        (update.effective_chat.id, context.user_data['event_name'], context.user_data['event_date'], final_time)
    )
    conn.commit()
    conn.close()
    
    await update.message.reply_text(
        f"✅ Saved: <b>{secure_text(context.user_data['event_name'])}</b> on {context.user_data['event_date']}!",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END

# --- ADD NOTE LOGIC ---

async def add_note_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 1: Ask for Note Title."""
    await update.message.reply_text(
        "📝 New Note: What is the <b>Title</b>?", 
        parse_mode=ParseMode.HTML, 
        reply_markup=get_back_keyboard()
    )
    return NOTE_TITLE

async def get_note_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 2: Ask for Note Content (Text or Photo)."""
    context.user_data['note_title'] = update.message.text
    await update.message.reply_text("Got it. Send <b>Text</b> or a <b>Photo</b>.", parse_mode=ParseMode.HTML)
    return NOTE_CONTENT

async def get_note_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 3: Save the note/photo to Database."""
    photo_id = None
    content = ""

    # Check if user sent a photo
    if update.message.photo:
        # Get the highest resolution photo
        photo_id = update.message.photo[-1].file_id
        if update.message.caption:
            content = update.message.caption
    else:
        content = update.message.text

    conn = sqlite3.connect("dates.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO notes (chat_id, title, content, photo_id) VALUES (?, ?, ?, ?)",
        (update.effective_chat.id, context.user_data['note_title'], content, photo_id)
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Note saved!", reply_markup=get_main_keyboard())
    return ConversationHandler.END

# --- LIST DATA LOGIC ---

async def list_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetches and displays all saved events."""
    conn = sqlite3.connect("dates.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name, event_date, notify_time FROM events WHERE chat_id = ?", (update.effective_chat.id,))
    events = cursor.fetchall()
    
    # Also fetch the user's timezone to display it
    cursor.execute("SELECT timezone FROM user_settings WHERE chat_id = ?", (update.effective_chat.id,))
    tz_row = cursor.fetchone()
    user_tz = tz_row[0] if tz_row else "UTC (Default)"
    
    conn.close()

    if not events:
        await update.message.reply_text("No dates saved yet!", reply_markup=get_main_keyboard())
    else:
        message = f"📅 <b>Your Important Dates:</b>\n(Timezone: {user_tz})\n\n"
        for name, date_str, time_str in events:
            message += f"• {secure_text(name)}: {date_str} (Alert: {time_str})\n"
            
        await update.message.reply_text(message, parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard())

async def list_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetches and displays all saved notes/photos."""
    conn = sqlite3.connect("dates.db")
    cursor = conn.cursor()
    cursor.execute("SELECT title, content, photo_id FROM notes WHERE chat_id = ?", (update.effective_chat.id,))
    notes = cursor.fetchall()
    conn.close()

    if not notes:
        await update.message.reply_text("No notes saved yet!", reply_markup=get_main_keyboard())
        return

    text_notes_message = "📝 <b>Your Saved Notes:</b>\n\n"
    image_notes_list = []
    has_text_notes = False

    # Separate text notes from image notes for cleaner display
    for title, content, photo_id in notes:
        if photo_id:
            image_notes_list.append((title, content, photo_id))
        else:
            has_text_notes = True
            text_notes_message += f"📌 <b>{secure_text(title)}</b>\n"
            if content:
                text_notes_message += f"<code>{secure_text(content)}</code>\n\n"

    # Send text notes in one big message
    if has_text_notes:
        await update.message.reply_text(text_notes_message, parse_mode=ParseMode.HTML)
    elif not image_notes_list:
        await update.message.reply_text("No text notes found.")

    # Send images one by one
    for title, content, photo_id in image_notes_list:
        caption = f"📌 <b>{secure_text(title)}</b>"
        if content:
            caption += f"\n{secure_text(content)}"
        await update.message.reply_photo(photo=photo_id, caption=caption, parse_mode=ParseMode.HTML)
        
    await update.message.reply_text("Done.", reply_markup=get_main_keyboard())

# --- DELETION LOGIC ---

async def delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 1: Ask user what category to delete (Date or Note)."""
    keyboard = [
        [KeyboardButton("Delete Date"), KeyboardButton("Delete Note")],
        [KeyboardButton("🔙 Back")]
    ]
    await update.message.reply_text(
        "What would you like to delete?", 
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return DELETE_CHOICE

async def delete_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 2: List items with IDs and handle deletion."""
    text = update.message.text
    conn = sqlite3.connect("dates.db")
    cursor = conn.cursor()
    
    # CASE A: User chose "Delete Date" -> Show list of dates
    if text == "Delete Date":
        context.user_data['delete_type'] = 'event'
        cursor.execute("SELECT id, name, event_date FROM events WHERE chat_id = ?", (update.effective_chat.id,))
        rows = cursor.fetchall()
        if not rows:
            await update.message.reply_text("No dates to delete.", reply_markup=get_back_keyboard())
            conn.close()
            return DELETE_CHOICE
            
        msg = "🗑 <b>Reply with the ID to delete:</b>\n\n"
        for r_id, name, date_str in rows:
            msg += f"ID: <b>{r_id}</b> | {secure_text(name)} ({date_str})\n"
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_back_keyboard())
        conn.close()
        return DELETE_CHOICE

    # CASE B: User chose "Delete Note" -> Show list of notes
    elif text == "Delete Note":
        context.user_data['delete_type'] = 'note'
        cursor.execute("SELECT id, title FROM notes WHERE chat_id = ?", (update.effective_chat.id,))
        rows = cursor.fetchall()
        if not rows:
            await update.message.reply_text("No notes to delete.", reply_markup=get_back_keyboard())
            conn.close()
            return DELETE_CHOICE
            
        msg = "🗑 <b>Reply with the ID to delete:</b>\n\n"
        for r_id, title in rows:
            msg += f"ID: <b>{r_id}</b> | {secure_text(title)}\n"
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_back_keyboard())
        conn.close()
        return DELETE_CHOICE
    
    # CASE C: User sent a Number (ID) -> Delete that item
    try:
        item_id = int(text)
        table = "events" if context.user_data.get('delete_type') == 'event' else "notes"
        
        cursor.execute(f"DELETE FROM {table} WHERE id = ? AND chat_id = ?", (item_id, update.effective_chat.id))
        
        if cursor.rowcount > 0:
            await update.message.reply_text("✅ Deleted successfully.", reply_markup=get_main_keyboard())
            conn.commit()
            conn.close()
            return ConversationHandler.END
        else:
            await update.message.reply_text("❌ Could not find that ID. Try again or click Back.", reply_markup=get_back_keyboard())
            conn.close()
            return DELETE_CHOICE
    except ValueError:
        await update.message.reply_text("Please select an option or enter a valid ID number.", reply_markup=get_back_keyboard())
        return DELETE_CHOICE

# --- NOTIFICATION SYSTEM (CRON JOB) ---

async def check_reminders(application: Application):
    """
    Runs every 60 seconds. Checks if any event matches the current time
    in the user's specific timezone.
    """
    conn = sqlite3.connect("dates.db")
    conn.row_factory = sqlite3.Row  # Allows accessing columns by name
    cursor = conn.cursor()
    
    # Get all events, plus the user's timezone setting (using LEFT JOIN)
    cursor.execute("""
        SELECT e.chat_id, e.name, e.event_date, e.notify_time, u.timezone
        FROM events e
        LEFT JOIN user_settings u ON e.chat_id = u.chat_id
    """)
    rows = cursor.fetchall()
    conn.close()

    # Current Server Time (UTC)
    utc_now = datetime.now(pytz.utc)

    for row in rows:
        chat_id = row['chat_id']
        name = row['name']
        date_str = row['event_date']
        notify_time = row['notify_time']
        
        # Determine User's Timezone (Default to UTC if they haven't set one)
        tz_name = row['timezone'] if row['timezone'] else 'UTC'
        try:
            user_tz = pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            user_tz = pytz.utc
        
        # Convert UTC time to User's Local Time
        user_now = utc_now.astimezone(user_tz)
        user_today = user_now.date()
        user_time_str = user_now.strftime("%H:%M")

        # 1. Check if the TIME matches
        if user_time_str != notify_time:
            continue

        # 2. Check if the DATE matches (handling yearly recurrences)
        try:
            event_dt = datetime.strptime(date_str, "%d-%m-%Y").date()
            # Pretend the event is happening this year
            this_year_event = event_dt.replace(year=user_today.year)
            
            # If the event passed already this year, look at next year
            if this_year_event < user_today:
                this_year_event = event_dt.replace(year=user_today.year + 1)
            
            days_until = (this_year_event - user_today).days

            # 3. Create the message based on how close the event is
            message = None
            if days_until == 30:
                message = f"🔔 Head's up! <b>{secure_text(name)}</b> is in 1 month."
            elif 0 < days_until < 30 and days_until % 7 == 0:
                weeks = days_until // 7
                message = f"⏰ Reminder: <b>{secure_text(name)}</b> is in {weeks} week(s)."
            elif days_until == 1:
                message = f"😱 Get ready! <b>{secure_text(name)}</b> is TOMORROW!"
            elif days_until == 0:
                message = f"🎉 Today is the day! Happy <b>{secure_text(name)}</b>!"

            # 4. Send the message
            if message:
                await application.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.HTML)
        except ValueError:
            continue

async def post_init(application: Application):
    """Starts the background scheduler when the bot launches."""
    scheduler = AsyncIOScheduler()
    # run check_reminders every 60 seconds
    scheduler.add_job(check_reminders, "interval", seconds=60, args=[application])
    scheduler.start()

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and handle specific connection issues."""
    
    # If the error is a network issue, just log a warning (don't crash)
    if isinstance(context.error, NetworkError):
        logging.warning(f"Network Error: {context.error} (The bot will retry automatically)")
        return

    # If the error is Forbidden (user blocked bot), strictly log it
    if isinstance(context.error, Forbidden):
        logging.error(f"User blocked the bot: {context.error}")
        return

    # For all other unknown errors, print the full traceback so we can debug later
    logging.error("Exception while handling an update:", exc_info=context.error)

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    # 1. Initialize Database (Create tables if missing)
    init_db()
    
    # 2. Build the Bot Application
    application = Application.builder().token(TOKEN).post_init(post_init).build()

    # 3. Connect Commands & Handlers
    application.add_handler(CommandHandler("start", start))
    
    # Menu Button Handlers
    application.add_handler(MessageHandler(filters.Regex("^📅 List Dates$"), list_events))
    application.add_handler(MessageHandler(filters.Regex("^📝 View Notes$"), list_notes))
    application.add_handler(MessageHandler(filters.Regex("^❤️ Our Journey$"), our_journey))
    
    # Filter: Captures text that IS NOT a command and IS NOT the "Back" button
    text_filter = filters.TEXT & ~filters.COMMAND & ~filters.Regex("^🔙 Back$")

    # Conversation: Set Timezone
    application.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("timezone", timezone_start),
            MessageHandler(filters.Regex("^🌍 Set Timezone$"), timezone_start)
        ],
        states={
            SET_TIMEZONE: [MessageHandler(text_filter, save_timezone)],
        },
        fallbacks=[
            CommandHandler("cancel", back_to_menu),
            MessageHandler(filters.Regex("^🔙 Back$"), back_to_menu)
        ],
    ))

    # Conversation: Add Event
    application.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("add", add_event_start),
            MessageHandler(filters.Regex("^➕ Add Date$"), add_event_start)
        ],
        states={
            NAME: [MessageHandler(text_filter, get_name)],
            DATE: [MessageHandler(text_filter, get_date)],
            TIME: [MessageHandler(text_filter, get_time)],
        },
        fallbacks=[
            CommandHandler("cancel", back_to_menu),
            MessageHandler(filters.Regex("^🔙 Back$"), back_to_menu)
        ],
    ))

    # Conversation: Add Note
    application.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("addnote", add_note_start),
            MessageHandler(filters.Regex("^➕ Add Note$"), add_note_start)
        ],
        states={
            NOTE_TITLE: [MessageHandler(text_filter, get_note_title)],
            NOTE_CONTENT: [MessageHandler((text_filter | filters.PHOTO), get_note_content)],
        },
        fallbacks=[
            CommandHandler("cancel", back_to_menu),
            MessageHandler(filters.Regex("^🔙 Back$"), back_to_menu)
        ],
    ))

    # Conversation: Delete Item
    application.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("delete", delete_start),
            MessageHandler(filters.Regex("^🗑 Delete Item$"), delete_start)
        ],
        states={DELETE_CHOICE: [MessageHandler(text_filter, delete_router)]},
        fallbacks=[
            CommandHandler("cancel", back_to_menu),
            MessageHandler(filters.Regex("^🔙 Back$"), back_to_menu)
        ],
    ))

    # Global Error Handler
    application.add_error_handler(error_handler)

    # 4. Run the Bot
    print("Bot is running...")
    application.run_polling()
    