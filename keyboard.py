"""Keyboard layouts for the bot."""

from telegram import ReplyKeyboardMarkup, KeyboardButton


def get_main_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("📅 List Dates"), KeyboardButton("➕ Add Date")],
        [KeyboardButton("📝 View Notes"), KeyboardButton("➕ Add Note")],
        [KeyboardButton("✏️ Edit Date"), KeyboardButton("✏️ Edit Note")],
        [KeyboardButton("🗑 Delete Item"), KeyboardButton("❤️ Our Journey")],
        [KeyboardButton("🌍 Set Timezone")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_back_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🔙 Back")]], resize_keyboard=True
    )


def get_timezone_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("Asia/Singapore"), KeyboardButton("UTC")],
        [KeyboardButton("US/Eastern"), KeyboardButton("Europe/London")],
        [KeyboardButton("🔙 Back")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)


def get_delete_choice_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("Delete Date"), KeyboardButton("Delete Note")],
        [KeyboardButton("🔙 Back")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)


def get_event_field_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("Name"), KeyboardButton("Date"), KeyboardButton("Time")],
        [KeyboardButton("🔙 Back")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)


def get_note_field_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("Title"), KeyboardButton("Content")],
        [KeyboardButton("🔙 Back")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
