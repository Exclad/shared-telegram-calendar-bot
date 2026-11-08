"""Keyboard layouts for the bot."""

import calendar

from telegram import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton


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
        [KeyboardButton("Asia/Singapore"), KeyboardButton("Asia/Tokyo"), KeyboardButton("Asia/Kolkata")],
        [KeyboardButton("Europe/London"), KeyboardButton("Europe/Berlin"), KeyboardButton("Europe/Paris")],
        [KeyboardButton("US/Eastern"), KeyboardButton("US/Central"), KeyboardButton("US/Pacific")],
        [KeyboardButton("Australia/Sydney"), KeyboardButton("UTC")],
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
        [KeyboardButton("Recurring"), KeyboardButton("Reminders")],
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

def _build_numbered_list(items: list[dict], item_type: str, page: int) -> InlineKeyboardMarkup | None:
    """Match the numbers in the message; keep titles out of narrow buttons."""
    if not items:
        return None
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(str(number), callback_data=f"view|{item_type}|{item['id']}|{page}")
        for number, item in enumerate(items, 1)
    ]])


def build_event_list_inline(events: list[dict], page: int = 1) -> InlineKeyboardMarkup | None:
    return _build_numbered_list(events, "event", page)


def build_note_list_inline(notes: list[dict], page: int = 1) -> InlineKeyboardMarkup | None:
    return _build_numbered_list(notes, "note", page)


def build_item_detail_inline(
    item_type: str, item_id: int, page: int, part: int = 0, total_parts: int = 1,
) -> InlineKeyboardMarkup:
    rows = []
    if total_parts > 1:
        nav = []
        if part > 0:
            nav.append(InlineKeyboardButton(
                "‹ Previous", callback_data=f"view|{item_type}|{item_id}|{page}|{part - 1}"
            ))
        nav.append(InlineKeyboardButton(f"{part + 1}/{total_parts}", callback_data="pg_noop"))
        if part + 1 < total_parts:
            nav.append(InlineKeyboardButton(
                "Next ›", callback_data=f"view|{item_type}|{item_id}|{page}|{part + 1}"
            ))
        rows.append(nav)
    rows.append([
        InlineKeyboardButton("✏️ Edit", callback_data=f"edit|{item_type}|{item_id}"),
        InlineKeyboardButton("🗑 Delete", callback_data=f"del|{item_type}|{item_id}|{page}"),
    ])
    rows.extend(build_list_back_inline(item_type, page).inline_keyboard)
    return InlineKeyboardMarkup(rows)


def build_list_back_inline(item_type: str, page: int) -> InlineKeyboardMarkup:
    prefix = "ev" if item_type == "event" else "nt"
    label = "‹ Back to dates" if item_type == "event" else "‹ Back to notes"
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"pg|{prefix}|{page}")]])


def build_photo_note_inline(note: dict) -> InlineKeyboardMarkup:
    """Edit/Delete buttons attached to a single photo note message."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✏️ Edit", callback_data=f"edit|note|{note['id']}"),
        InlineKeyboardButton("🗑 Delete", callback_data=f"del|note|{note['id']}"),
    ]])


# ── Inline keyboards for inline actions ────────────────

def build_confirm_delete_inline(item_type: str, item_id: int, page: int | None = None) -> InlineKeyboardMarkup:
    suffix = f"|{page}" if page is not None else ""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Yes, delete",
                callback_data=f"confirm_del|{item_type}|{item_id}{suffix}",
            ),
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data=f"view|{item_type}|{item_id}|{page}" if page is not None else "cancel_del",
            ),
        ]
    ])


def build_pagination_nav(page: int, total: int, prefix: str) -> list[list[InlineKeyboardButton]]:
    """Show navigation only when there is more than one page."""
    if total <= 1:
        return []
    page = max(1, min(page, total))
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
            [InlineKeyboardButton("Reminders", callback_data=f"field|event|{item_id}|Reminders")],
        ]
    else:
        buttons = [
            [InlineKeyboardButton("Title", callback_data=f"field|note|{item_id}|Title")],
            [InlineKeyboardButton("Content", callback_data=f"field|note|{item_id}|Content")],
        ]
    buttons.append([InlineKeyboardButton("Cancel", callback_data="cancel_edit")])
    return InlineKeyboardMarkup(buttons)


def build_snooze_inline(event_id: int) -> InlineKeyboardMarkup:
    """Snooze button attached to reminder messages."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⏰ Snooze 1h", callback_data=f"snz|{event_id}"),
    ]])


# ── Inline calendar date picker ─────────────────────────

def build_calendar_inline(year: int, month: int) -> InlineKeyboardMarkup:
    """Month-view calendar. Day press → cal|pick|Y|M|D, nav → cal|nav|Y|M."""
    header = [InlineKeyboardButton(
        f"{calendar.month_name[month]} {year}", callback_data="cal|noop"
    )]

    weekday_row = [
        InlineKeyboardButton(d, callback_data="cal|noop")
        for d in ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
    ]

    day_rows = []
    for week in calendar.monthcalendar(year, month):
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="cal|noop"))
            else:
                row.append(InlineKeyboardButton(
                    str(day), callback_data=f"cal|pick|{year}|{month}|{day}"
                ))
        day_rows.append(row)

    prev_y, prev_m = (year - 1, 12) if month == 1 else (year, month - 1)
    next_y, next_m = (year + 1, 1) if month == 12 else (year, month + 1)
    nav_row = [
        InlineKeyboardButton("«", callback_data=f"cal|nav|{year - 1}|{month}"),
        InlineKeyboardButton("‹", callback_data=f"cal|nav|{prev_y}|{prev_m}"),
        InlineKeyboardButton("Today", callback_data="cal|today"),
        InlineKeyboardButton("›", callback_data=f"cal|nav|{next_y}|{next_m}"),
        InlineKeyboardButton("»", callback_data=f"cal|nav|{year + 1}|{month}"),
    ]

    return InlineKeyboardMarkup([header, weekday_row, *day_rows, nav_row])
