"""Database operations with connection safety, retry logic, and parameterized queries.

Event dates are stored as ISO ``YYYY-MM-DD`` strings. ``migrate()`` converts
legacy ``DD-MM-YYYY`` rows in place.
"""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

from config import DB_PATH, DEFAULT_JOURNEY_EVENT, MAX_INPUT_LENGTH, MAX_NOTE_CONTENT_LENGTH
from utils import parse_iso_date, parse_reminder_days, parse_recurring, parse_time_hhmm

EVENT_FIELDS = {
    "Name": "name",
    "Date": "event_date",
    "Time": "notify_time",
    "Recurring": "recurring",
    "Reminders": "reminder_days",
}

NOTE_FIELDS = {
    "Title": "title",
    "Content": "content",
}


@contextmanager
def get_db():
    """Connection context manager with SQLite busy timeout configured."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist and enable WAL mode."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA wal_autocheckpoint=1000")
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
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_chat ON events(chat_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_notes_chat ON notes(chat_id)")
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

        # events.reminder_days — optional per-event schedule ("30,7,1,0")
        if "reminder_days" not in columns:
            cursor.execute("ALTER TABLE events ADD COLUMN reminder_days TEXT")

        # user_settings.journey_event (legacy, name-based) and journey_event_id
        cursor.execute("PRAGMA table_info(user_settings)")
        us_cols = {col[1] for col in cursor.fetchall()}
        if "journey_event" not in us_cols:
            cursor.execute("ALTER TABLE user_settings ADD COLUMN journey_event TEXT")
        if "journey_event_id" not in us_cols:
            cursor.execute("ALTER TABLE user_settings ADD COLUMN journey_event_id INTEGER")

        # Legacy DD-MM-YYYY dates → ISO YYYY-MM-DD
        cursor.execute("SELECT id, event_date FROM events")
        for row in cursor.fetchall():
            raw = row["event_date"]
            if parse_iso_date(raw):
                continue
            try:
                iso = datetime.strptime(raw, "%d-%m-%Y").date().isoformat()
            except ValueError:
                logger.warning("Unparseable legacy event_date %r (id=%s); left as-is", raw, row["id"])
                continue
            cursor.execute("UPDATE events SET event_date = ? WHERE id = ?", (iso, row["id"]))

        # Resolve legacy name-based journey settings to event ids
        cursor.execute(
            "SELECT chat_id, journey_event FROM user_settings "
            "WHERE journey_event IS NOT NULL AND journey_event_id IS NULL"
        )
        for row in cursor.fetchall():
            cursor.execute(
                "SELECT id FROM events WHERE chat_id = ? AND name = ? COLLATE NOCASE "
                "ORDER BY id LIMIT 1",
                (row["chat_id"], row["journey_event"]),
            )
            ev = cursor.fetchone()
            if ev:
                cursor.execute(
                    "UPDATE user_settings SET journey_event_id = ? WHERE chat_id = ?",
                    (ev["id"], row["chat_id"]),
                )

        conn.commit()


def _truncate(value: str, max_len: int) -> str:
    """Truncate input to max length, logging a warning when truncation occurs."""
    if not value:
        return ""
    if len(value) > max_len:
        logger.warning("Input truncated from %d to %d chars", len(value), max_len)
        return value[:max_len]
    return value


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
              recurring: bool = True, reminder_days: Optional[str] = None) -> int:
    """Insert an event. ``event_date`` must be an ISO YYYY-MM-DD string."""
    name = _truncate(name, MAX_INPUT_LENGTH)
    if not parse_iso_date(event_date):
        raise ValueError(f"event_date must be ISO YYYY-MM-DD, got {event_date!r}")
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO events (chat_id, name, event_date, notify_time, recurring, reminder_days) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, name, event_date, notify_time, int(recurring), reminder_days),
        )
        conn.commit()
        return cursor.lastrowid


def get_events(chat_id: int) -> list[dict]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name, event_date, notify_time, recurring, reminder_days "
            "FROM events WHERE chat_id = ? ORDER BY id",
            (chat_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_event(chat_id: int, event_id: int) -> Optional[dict]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name, event_date, notify_time, recurring, reminder_days "
            "FROM events WHERE id = ? AND chat_id = ?",
            (event_id, chat_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def update_event(chat_id: int, event_id: int, field: str, value: str) -> bool:
    """Update an event field by display name. Returns True if a row was changed.

    Values are validated strictly; invalid values raise ValueError. ``Date``
    must already be an ISO YYYY-MM-DD string (handlers convert user input).
    """
    column = EVENT_FIELDS.get(field)
    if not column:
        raise ValueError(f"Invalid event field: {field}")

    if column == "recurring":
        parsed = parse_recurring(value)
        if parsed is None:
            raise ValueError(f"Invalid recurring value: {value!r}")
        value = "1" if parsed else "0"
    elif column == "name":
        value = _truncate(value.strip(), MAX_INPUT_LENGTH)
        if not value:
            raise ValueError("Event name cannot be empty")
    elif column == "event_date":
        if not parse_iso_date(value):
            raise ValueError(f"Invalid ISO date: {value!r}")
    elif column == "notify_time":
        normalized = parse_time_hhmm(value)
        if not normalized:
            raise ValueError(f"Invalid time: {value!r}")
        value = normalized
    elif column == "reminder_days":
        days = parse_reminder_days(value)
        if days is None:
            raise ValueError(f"Invalid reminder days: {value!r}")
        value = ",".join(str(d) for d in days)
    else:
        raise ValueError(f"Unexpected event column: {column}")

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
    if column == "title":
        value = _truncate(value.strip(), MAX_INPUT_LENGTH)
        if not value:
            raise ValueError("Note title cannot be empty")
    else:
        value = _truncate(value, MAX_NOTE_CONTENT_LENGTH)

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
    """Return the configured journey event *name* (for display)."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT journey_event FROM user_settings WHERE chat_id = ?",
            (chat_id,),
        )
        row = cursor.fetchone()
        return row["journey_event"] if row and row["journey_event"] else DEFAULT_JOURNEY_EVENT


def set_journey_event(chat_id: int, event_id: int, event_name: str):
    """Store the journey event by id (name kept for display/fallback)."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO user_settings (chat_id, timezone, journey_event, journey_event_id) "
            "VALUES (?, 'UTC', ?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET "
            "journey_event = excluded.journey_event, "
            "journey_event_id = excluded.journey_event_id",
            (chat_id, event_name, event_id),
        )
        conn.commit()


# ── Reminder queries ────────────────────────────────────

def get_all_events_with_timezone() -> list[dict]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT e.id, e.chat_id, e.name, e.event_date, e.notify_time,
                   e.recurring, e.reminder_days, u.timezone
            FROM events e
            LEFT JOIN user_settings u ON e.chat_id = u.chat_id
        """)
        return [dict(row) for row in cursor.fetchall()]


def get_journey_event_for_chat(chat_id: int) -> tuple[Optional[str], str]:
    """Return (event_date_iso, event_name) for the chat's journey event.

    Resolution order: configured event id → exact (case-insensitive) match on
    the configured/default name. Returns (None, name) if nothing matches.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT journey_event, journey_event_id FROM user_settings WHERE chat_id = ?",
            (chat_id,),
        )
        settings = cursor.fetchone()
        name = (settings["journey_event"] if settings and settings["journey_event"]
                else DEFAULT_JOURNEY_EVENT)

        if settings and settings["journey_event_id"]:
            cursor.execute(
                "SELECT name, event_date FROM events WHERE id = ? AND chat_id = ?",
                (settings["journey_event_id"], chat_id),
            )
            row = cursor.fetchone()
            if row:
                return row["event_date"], row["name"]

        cursor.execute(
            "SELECT name, event_date FROM events "
            "WHERE chat_id = ? AND name = ? COLLATE NOCASE ORDER BY id LIMIT 1",
            (chat_id, name),
        )
        row = cursor.fetchone()
        return (row["event_date"], row["name"]) if row else (None, name)
