"""Keyboard layouts for the bot."""

from telegram import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton


def _button_label(prefix: str, value: str, limit: int = 30) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        text = f"{text[:limit - 1]}…"
    return f"{prefix} {text}"


def get_main_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("📅 List Dates"), KeyboardButton("➕ Add Date")],
        [KeyboardButton("📝 View Notes"), KeyboardButton("➕ Add Note")],
        [KeyboardButton("🔍 Upcoming"), KeyboardButton("❤️ Our Journey")],
        [KeyboardButton("✏️ Edit Date"), KeyboardButton("✏️ Edit Note")],
        [KeyboardButton("🗑 Delete Item"), KeyboardButton("📤 Export")],
        [KeyboardButton("🌍 Set Timezone"), KeyboardButton("⚙️ Journey Event")],
        [KeyboardButton("❓ Help")],
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
        [KeyboardButton("Recurring")],
        [KeyboardButton("🔙 Back")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)


def get_note_field_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("Title"), KeyboardButton("Content")],
        [KeyboardButton("🔙 Back")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)


def get_recurring_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("Yes (Recurring)"), KeyboardButton("No (One-time)")],
        [KeyboardButton("🔙 Back")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)


# ── Inline keyboards for list views ────────────────────

def build_event_list_inline(events: list[dict]) -> InlineKeyboardMarkup | None:
    """Build inline keyboard with Edit/Delete buttons per event."""
    if not events:
        return None
    buttons = []
    for ev in events:
        row = [
            InlineKeyboardButton(
                _button_label("✏️", ev["name"]),
                callback_data=f"edit|event|{ev['id']}",
            ),
            InlineKeyboardButton(
                f"🗑 Del",
                callback_data=f"del|event|{ev['id']}",
            ),
        ]
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


def build_note_list_inline(notes: list[dict]) -> InlineKeyboardMarkup | None:
    """Build inline keyboard with Edit/Delete buttons per note."""
    if not notes:
        return None
    buttons = []
    for note in notes:
        row = [
            InlineKeyboardButton(
                _button_label("✏️", note["title"]),
                callback_data=f"edit|note|{note['id']}",
            ),
            InlineKeyboardButton(
                f"🗑 Del",
                callback_data=f"del|note|{note['id']}",
            ),
        ]
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


# ── Inline keyboards for inline actions ────────────────

def build_confirm_delete_inline(item_type: str, item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Yes, delete",
                callback_data=f"confirm_del|{item_type}|{item_id}",
            ),
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data="cancel_del",
            ),
        ]
    ])


def build_pagination_nav(page: int, total: int, prefix: str) -> list[list[InlineKeyboardButton]]:
    """Return a navigation row for paginated lists."""
    row = []
    if page > 1:
        row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"pg|{prefix}|{page - 1}"))
    row.append(InlineKeyboardButton(f"{page}/{total}", callback_data="pg_noop"))
    if page < total:
        row.append(InlineKeyboardButton("Next ▶️", callback_data=f"pg|{prefix}|{page + 1}"))
    return [row] if row else []


def build_edit_field_inline(item_type: str, item_id: int) -> InlineKeyboardMarkup:
    if item_type == "event":
        buttons = [
            [InlineKeyboardButton("Name", callback_data=f"field|event|{item_id}|Name")],
            [InlineKeyboardButton("Date", callback_data=f"field|event|{item_id}|Date")],
            [InlineKeyboardButton("Time", callback_data=f"field|event|{item_id}|Time")],
            [InlineKeyboardButton("Recurring", callback_data=f"field|event|{item_id}|Recurring")],
        ]
    else:
        buttons = [
            [InlineKeyboardButton("Title", callback_data=f"field|note|{item_id}|Title")],
            [InlineKeyboardButton("Content", callback_data=f"field|note|{item_id}|Content")],
        ]
    buttons.append([InlineKeyboardButton("Cancel", callback_data="cancel_edit")])
    return InlineKeyboardMarkup(buttons)
