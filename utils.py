"""Shared date parsing/formatting helpers.

Dates are stored in the database as ISO ``YYYY-MM-DD`` strings (sortable,
unambiguous). Users type and see ``DD-MM-YYYY``.
"""

from datetime import date, datetime

# Formats accepted from user input, tried in order.
_INPUT_FORMATS = [
    "%d-%m-%Y",   # 17-09-2022
    "%d/%m/%Y",   # 17/09/2022
    "%d.%m.%Y",   # 17.09.2022
    "%Y-%m-%d",   # 2022-09-17 (ISO)
    "%d %b %Y",   # 17 Sep 2022
    "%d %B %Y",   # 17 September 2022
]

DATE_INPUT_HINT = "DD-MM-YYYY (e.g., 17-09-2022)"


def parse_user_date(text: str) -> date | None:
    """Parse a user-typed date in any accepted format. Returns None if invalid."""
    text = " ".join((text or "").strip().split())
    for fmt in _INPUT_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_iso_date(value: str) -> date | None:
    """Parse a stored ISO date string. Returns None if malformed."""
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def to_iso(d: date) -> str:
    return d.isoformat()


def format_display(value: "date | str") -> str:
    """Format a date (or stored ISO string) as DD-MM-YYYY for display."""
    if isinstance(value, str):
        parsed = parse_iso_date(value)
        if parsed is None:
            return value  # show raw rather than crash on legacy/bad data
        value = parsed
    return value.strftime("%d-%m-%Y")


def parse_time_hhmm(text: str) -> str | None:
    """Validate/normalize an HH:MM time string. Returns 'HH:MM' or None."""
    try:
        t = datetime.strptime((text or "").strip(), "%H:%M")
        return t.strftime("%H:%M")
    except ValueError:
        return None


def parse_reminder_days(text: str, max_entries: int = 20) -> list[int] | None:
    """Parse a comma-separated list of day offsets (0-365). None if invalid."""
    parts = [p.strip() for p in (text or "").split(",") if p.strip()]
    if not parts or len(parts) > max_entries:
        return None
    days = []
    for p in parts:
        if not p.lstrip("-").isdigit():
            return None
        n = int(p)
        if n < 0 or n > 365:
            return None
        days.append(n)
    return sorted(set(days), reverse=True)


def parse_recurring(text: str) -> bool | None:
    """Parse a yes/no recurring answer. None if unrecognized."""
    t = (text or "").strip().lower()
    if t in ("yes", "y", "true", "1", "recurring") or t.startswith("yes"):
        return True
    if t in ("no", "n", "false", "0", "one-time", "onetime") or t.startswith("no"):
        return False
    return None
