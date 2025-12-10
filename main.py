import logging
import sqlite3
import os
import html
from datetime import datetime, date
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- CONFIGURATION ---
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TOKEN:
    print("Error: TELEGRAM_TOKEN not found in .env file.")
    exit()

# 1. Basic Logging Setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# 2. SILENCE THE NOISY LOGS (The Fix)
# This hides the constant "HTTP Request: POST..." messages
logging.getLogger("httpx").setLevel(logging.WARNING)
# This hides the constant "Job check_reminders executed..." messages
logging.getLogger("apscheduler").setLevel(logging.WARNING)

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect("dates.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            name TEXT,
            event_date TEXT,
            notify_time TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            title TEXT,
            content TEXT,
            photo_id TEXT
        )
    """)
    conn.commit()
    conn.close()

# --- CONVERSATION STATES ---
NAME, DATE, TIME = range(3)
NOTE_TITLE, NOTE_CONTENT = range(3, 5)
DELETE_CHOICE = 5

# --- HELPERS: KEYBOARDS ---
def get_main_keyboard():
    keyboard = [
        [KeyboardButton("📅 List Dates"), KeyboardButton("➕ Add Date")],
        [KeyboardButton("📝 View Notes"), KeyboardButton("➕ Add Note")],
        [KeyboardButton("❤️ Our Journey"), KeyboardButton("🗑 Delete Item")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_back_keyboard():
    """Shows only a Back button"""
    return ReplyKeyboardMarkup([[KeyboardButton("🔙 Back")]], resize_keyboard=True)

def secure_text(text):
    if text:
        return html.escape(text)
    return ""

# --- GENERIC COMMANDS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **Hello!**\n\n"
        "I am ready to track your important memories.\n"
        "Use the buttons below to control me.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_keyboard()
    )

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels current operation and returns to main menu"""
    await update.message.reply_text("🔙 Returned to Main Menu.", reply_markup=get_main_keyboard())
    return ConversationHandler.END

# --- OUR JOURNEY LOGIC ---

async def our_journey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("dates.db")
    cursor = conn.cursor()
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

# --- EVENT LOGIC (ADD) ---

async def add_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "What is the name of the event? (e.g., Anniversary)", 
        reply_markup=get_back_keyboard()
    )
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['event_name'] = update.message.text
    await update.message.reply_text("Great! What is the date? Format: DD-MM-YYYY (e.g., 17-09-2022)")
    return DATE

async def get_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date_text = update.message.text
    try:
        datetime.strptime(date_text, "%d-%m-%Y") 
        context.user_data['event_date'] = date_text
        await update.message.reply_text("Date saved! Now, what time to remind? (Format: HH:MM)\nType 'skip' for 12:00 PM.")
        return TIME
    except ValueError:
        await update.message.reply_text("Invalid format. Please use DD-MM-YYYY.")
        return DATE

async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

# --- NOTE LOGIC (ADD) ---

async def add_note_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 New Note: What is the <b>Title</b>?", 
        parse_mode=ParseMode.HTML, 
        reply_markup=get_back_keyboard()
    )
    return NOTE_TITLE

async def get_note_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['note_title'] = update.message.text
    await update.message.reply_text("Got it. Send <b>Text</b> or a <b>Photo</b>.", parse_mode=ParseMode.HTML)
    return NOTE_CONTENT

async def get_note_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_id = None
    content = ""

    if update.message.photo:
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

# --- LIST LOGIC ---

async def list_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("dates.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name, event_date, notify_time FROM events WHERE chat_id = ?", (update.effective_chat.id,))
    events = cursor.fetchall()
    conn.close()

    if not events:
        await update.message.reply_text("No dates saved yet!", reply_markup=get_main_keyboard())
    else:
        message = "📅 <b>Your Important Dates:</b>\n\n"
        for name, date_str, time_str in events:
            message += f"• {secure_text(name)}: {date_str} (Alert: {time_str})\n"
            
        await update.message.reply_text(message, parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard())

async def list_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    for title, content, photo_id in notes:
        if photo_id:
            image_notes_list.append((title, content, photo_id))
        else:
            has_text_notes = True
            text_notes_message += f"📌 <b>{secure_text(title)}</b>\n"
            if content:
                text_notes_message += f"<code>{secure_text(content)}</code>\n\n"

    if has_text_notes:
        await update.message.reply_text(text_notes_message, parse_mode=ParseMode.HTML)
    elif not image_notes_list:
        await update.message.reply_text("No text notes found.")

    for title, content, photo_id in image_notes_list:
        caption = f"📌 <b>{secure_text(title)}</b>"
        if content:
            caption += f"\n{secure_text(content)}"
        await update.message.reply_photo(photo=photo_id, caption=caption, parse_mode=ParseMode.HTML)
        
    await update.message.reply_text("Done.", reply_markup=get_main_keyboard())

# --- DELETION LOGIC ---

async def delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This keyboard includes the Options AND the Back button
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
    text = update.message.text
    conn = sqlite3.connect("dates.db")
    cursor = conn.cursor()
    
    if text == "Delete Date":
        context.user_data['delete_type'] = 'event'
        cursor.execute("SELECT id, name, event_date FROM events WHERE chat_id = ?", (update.effective_chat.id,))
        rows = cursor.fetchall()
        if not rows:
            await update.message.reply_text("No dates to delete.", reply_markup=get_back_keyboard())
            conn.close()
            # Don't end, let them choose again or click back
            return DELETE_CHOICE
            
        msg = "🗑 <b>Reply with the ID to delete:</b>\n\n"
        for r_id, name, date_str in rows:
            msg += f"ID: <b>{r_id}</b> | {secure_text(name)} ({date_str})\n"
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_back_keyboard())
        conn.close()
        return DELETE_CHOICE

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
    
    # Handle the actual ID input
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

# --- NOTIFICATION LOGIC ---
async def check_reminders(application: Application):
    conn = sqlite3.connect("dates.db")
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id, name, event_date, notify_time FROM events")
    rows = cursor.fetchall()
    conn.close()

    now = datetime.now()
    today = now.date()
    current_time_str = now.strftime("%H:%M")

    for chat_id, name, date_str, notify_time in rows:
        if current_time_str != notify_time:
            continue
        try:
            event_dt = datetime.strptime(date_str, "%d-%m-%Y").date()
            this_year_event = event_dt.replace(year=today.year)
            if this_year_event < today:
                this_year_event = event_dt.replace(year=today.year + 1)
            
            days_until = (this_year_event - today).days

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

            if message:
                await application.bot.send_message(chat_id=chat_id, text=message, parse_mode=ParseMode.HTML)
        except ValueError:
            continue

async def post_init(application: Application):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_reminders, "interval", seconds=60, args=[application])
    scheduler.start()

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    init_db()
    application = Application.builder().token(TOKEN).post_init(post_init).build()

    # Commands & BUTTON HANDLERS
    application.add_handler(CommandHandler("start", start))
    
    application.add_handler(MessageHandler(filters.Regex("^📅 List Dates$"), list_events))
    application.add_handler(MessageHandler(filters.Regex("^📝 View Notes$"), list_notes))
    application.add_handler(MessageHandler(filters.Regex("^❤️ Our Journey$"), our_journey))
    
    # GLOBAL FILTER FOR INPUTS (Excludes Commands and "Back")
    # This is the secret sauce that makes "Back" work everywhere
    text_filter = filters.TEXT & ~filters.COMMAND & ~filters.Regex("^🔙 Back$")

    # Add Event
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

    # Add Note
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

    # Delete Item
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

    print("Bot is running...")
    application.run_polling()
