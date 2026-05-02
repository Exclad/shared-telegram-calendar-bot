"""Database operations with connection safety, retry logic, and parameterized queries."""

import sqlite3
import time
from contextlib import contextmanager
from typing import Optional

from config import DB_PATH, MAX_INPUT_LENGTH, MAX_NOTE_CONTENT_LENGTH

EVENT_FIELDS = {
    "Name": "name",
    "Date": "event_date",
    "Time": "notify_time",
    "Recurring": "recurring",
}

NOTE_FIELDS = {
    "Title": "title",
    "Content": "content",
}


@contextmanager
def get_db():
    """Connection context manager with retry on SQLITE_BUSY."""
    conn = None
    for attempt in range(3):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            yield conn
            return
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() or "busy" in str(e).lower():
                if conn:
                    conn.close()
                if attempt < 2:
                    time.sleep(0.1 * (attempt + 1))
                    continue
            raise
        finally:
            if conn:
                conn.close()


def init_db():
    """Create tables if they don't exist."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                event_date TEXT NOT NULL,
                notify_time TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                content TEXT,
                photo_id TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                chat_id INTEGER PRIMARY KEY,
                timezone TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_kv (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.commit()


def migrate():
    """Apply any missing schema migrations idempotently."""
    with get_db() as conn:
        cursor = conn.cursor()

        # events.recurring
        cursor.execute("PRAGMA table_info(events)")
        columns = {col[1] for col in cursor.fetchall()}
        if "recurring" not in columns:
            cursor.execute("ALTER TABLE events ADD COLUMN recurring BOOLEAN DEFAULT 1")

        # user_settings.journey_event
        cursor.execute("PRAGMA table_info(user_settings)")
        us_cols = {col[1] for col in cursor.fetchall()}
        if "journey_event" not in us_cols:
            cursor.execute("ALTER TABLE user_settings ADD COLUMN journey_event TEXT")

        conn.commit()


def _truncate(value: str, max_len: int) -> str:
    """Truncate input to max length."""
    return value[:max_len] if value else ""


# ── System key-value ────────────────────────────────────

def get_system_setting(key: str) -> Optional[str]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM system_kv WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row["value"] if row else None


def set_system_setting(key: str, value: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO system_kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


# ── Events ──────────────────────────────────────────────

def add_event(chat_id: int, name: str, event_date: str, notify_time: str,
              recurring: bool = True) -> int:
    name = _truncate(name, MAX_INPUT_LENGTH)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO events (chat_id, name, event_date, notify_time, recurring) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, name, event_date, notify_time, int(recurring)),
        )
        conn.commit()
        return cursor.lastrowid


def get_events(chat_id: int) -> list[dict]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name, event_date, notify_time, recurring FROM events "
            "WHERE chat_id = ? ORDER BY id",
            (chat_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_event(chat_id: int, event_id: int) -> Optional[dict]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name, event_date, notify_time, recurring FROM events "
            "WHERE id = ? AND chat_id = ?",
            (event_id, chat_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def update_event(chat_id: int, event_id: int, field: str, value: str) -> bool:
    """Update an event field by display name. Returns True if a row was changed."""
    column = EVENT_FIELDS.get(field)
    if not column:
        raise ValueError(f"Invalid event field: {field}")

    if column == "recurring":
        value = "1" if value.lower() in ("yes", "true", "1", "y", "recurring") else "0"
        max_len = 1
    elif column == "name":
        value = _truncate(value, MAX_INPUT_LENGTH)
    else:
        value = _truncate(value, 5)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE events SET {column} = ? WHERE id = ? AND chat_id = ?",
            (value, event_id, chat_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_event(chat_id: int, event_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM events WHERE id = ? AND chat_id = ?",
            (event_id, chat_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def get_anniversary_date(chat_id: int) -> Optional[str]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT event_date FROM events WHERE chat_id = ? AND name LIKE 'Anniversary%' LIMIT 1",
            (chat_id,),
        )
        row = cursor.fetchone()
        return row["event_date"] if row else None


# ── Notes ───────────────────────────────────────────────

def add_note(chat_id: int, title: str, content: str, photo_id: Optional[str] = None) -> int:
    title = _truncate(title, MAX_INPUT_LENGTH)
    content = _truncate(content, MAX_NOTE_CONTENT_LENGTH)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO notes (chat_id, title, content, photo_id) VALUES (?, ?, ?, ?)",
            (chat_id, title, content, photo_id),
        )
        conn.commit()
        return cursor.lastrowid


def get_notes(chat_id: int) -> list[dict]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, title, content, photo_id FROM notes WHERE chat_id = ? ORDER BY id",
            (chat_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_note(chat_id: int, note_id: int) -> Optional[dict]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, title, content, photo_id FROM notes WHERE id = ? AND chat_id = ?",
            (note_id, chat_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def update_note(chat_id: int, note_id: int, field: str, value: str,
                photo_id: Optional[str] = None) -> bool:
    """Update a note field by display name. Returns True if a row was changed."""
    column = NOTE_FIELDS.get(field)
    if not column:
        raise ValueError(f"Invalid note field: {field}")
    max_len = MAX_INPUT_LENGTH if column == "title" else MAX_NOTE_CONTENT_LENGTH
    value = _truncate(value, max_len)
    with get_db() as conn:
        cursor = conn.cursor()
        if field == "Content" and photo_id:
            cursor.execute(
                "UPDATE notes SET content = ?, photo_id = ? WHERE id = ? AND chat_id = ?",
                (value, photo_id, note_id, chat_id),
            )
        else:
            cursor.execute(
                f"UPDATE notes SET {column} = ? WHERE id = ? AND chat_id = ?",
                (value, note_id, chat_id),
            )
        conn.commit()
        return cursor.rowcount > 0


def delete_note(chat_id: int, note_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM notes WHERE id = ? AND chat_id = ?",
            (note_id, chat_id),
        )
        conn.commit()
        return cursor.rowcount > 0


# ── Settings ────────────────────────────────────────────

def get_timezone(chat_id: int) -> str:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT timezone FROM user_settings WHERE chat_id = ?",
            (chat_id,),
        )
        row = cursor.fetchone()
        return row["timezone"] if row else "UTC"


def set_timezone(chat_id: int, timezone: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO user_settings (chat_id, timezone) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET timezone = excluded.timezone",
            (chat_id, timezone),
        )
        conn.commit()


def get_journey_event(chat_id: int) -> str:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT journey_event FROM user_settings WHERE chat_id = ?",
            (chat_id,),
        )
        row = cursor.fetchone()
        return row["journey_event"] if row and row["journey_event"] else "Anniversary"


def set_journey_event(chat_id: int, event_name: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO user_settings (chat_id, timezone, journey_event) VALUES (?, 'UTC', ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET journey_event = excluded.journey_event",
            (chat_id, event_name),
        )
        conn.commit()


# ── Reminder queries ────────────────────────────────────

def get_all_events_with_timezone() -> list[dict]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT e.chat_id, e.name, e.event_date, e.notify_time, e.recurring, u.timezone
            FROM events e
            LEFT JOIN user_settings u ON e.chat_id = u.chat_id
        """)
        return [dict(row) for row in cursor.fetchall()]


def get_journey_event_for_chat(chat_id: int) -> tuple[Optional[str], Optional[str]]:
    """Return (event_date, event_name) for the chat's configured journey event."""
    event_name = get_journey_event(chat_id)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT event_date FROM events WHERE chat_id = ? AND name LIKE ? LIMIT 1",
            (chat_id, f"{event_name}%"),
        )
        row = cursor.fetchone()
        return (row["event_date"], event_name) if row else (None, event_name)
