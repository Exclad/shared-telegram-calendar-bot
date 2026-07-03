from datetime import datetime, timedelta, timezone as dt_timezone

from scheduler import (
    _allowed_days,
    _build_message,
    _days_and_years,
    _project_year_safe,
    compute_due_reminder,
)
from config import DEFAULT_REMINDER_DAYS
from utils import parse_iso_date


def utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=dt_timezone.utc)


def make_row(**kw):
    row = {
        "id": 1,
        "chat_id": 1,
        "name": "Anniversary",
        "event_date": "2020-09-17",
        "notify_time": "12:00",
        "recurring": 1,
        "reminder_days": None,
        "timezone": "UTC",
    }
    row.update(kw)
    return row


class TestBuildMessage:
    def test_day_of(self):
        assert "Today is the day" in _build_message("X", 0)

    def test_day_of_with_years(self):
        msg = _build_message("X", 0, years=5)
        assert "5 years" in msg

    def test_tomorrow(self):
        assert "TOMORROW" in _build_message("X", 1)

    def test_month(self):
        assert "1 month" in _build_message("X", 30)

    def test_weeks(self):
        assert "1 week." in _build_message("X", 7)
        assert "2 weeks" in _build_message("X", 14)

    def test_arbitrary_days(self):
        assert "in 10 days" in _build_message("X", 10)

    def test_negative(self):
        assert _build_message("X", -1) is None

    def test_escapes_html(self):
        assert "<b><script>" not in _build_message("<script>", 0)


class TestProjection:
    def test_leap_day(self):
        d = parse_iso_date("2020-02-29")
        assert _project_year_safe(d, 2023).day == 28

    def test_normal(self):
        d = parse_iso_date("2020-09-17")
        assert _project_year_safe(d, 2023) == parse_iso_date("2023-09-17")


class TestDaysAndYears:
    def test_recurring_years(self):
        event = parse_iso_date("2020-09-17")
        days, years = _days_and_years(event, True, parse_iso_date("2025-09-17"))
        assert days == 0
        assert years == 5

    def test_recurring_wraps_year(self):
        event = parse_iso_date("2020-01-01")
        days, years = _days_and_years(event, True, parse_iso_date("2025-12-31"))
        assert days == 1
        assert years == 6

    def test_one_time_passed(self):
        event = parse_iso_date("2020-01-01")
        assert _days_and_years(event, False, parse_iso_date("2025-01-01")) == (None, None)

    def test_one_time_future_no_years(self):
        event = parse_iso_date("2026-01-01")
        days, years = _days_and_years(event, False, parse_iso_date("2025-12-31"))
        assert days == 1
        assert years is None


class TestComputeDueReminder:
    def test_fires_in_window(self):
        row = make_row(event_date="2020-09-17", notify_time="12:00")
        now = utc(2025, 9, 17, 12, 0)
        due = compute_due_reminder(row, now, now - timedelta(minutes=1))
        assert due is not None
        assert due[0] == 0
        assert due[1] == 5  # years

    def test_not_due_outside_window(self):
        row = make_row(notify_time="12:00")
        now = utc(2025, 9, 17, 13, 0)
        assert compute_due_reminder(row, now, now - timedelta(minutes=1)) is None

    def test_midnight_gap_catchup(self):
        """Bug fix: reminder at 23:30, downtime 23:00 -> 00:30 next day."""
        row = make_row(event_date="2020-09-18", notify_time="23:30")
        now = utc(2025, 9, 18, 0, 30)          # just past midnight on the 18th
        last_check = utc(2025, 9, 17, 23, 0)   # went down before 23:30 on the 17th
        due = compute_due_reminder(row, now, last_check)
        assert due is not None
        # Fired for yesterday's slot: on the 17th, the Sep-18 event was 1 day away
        assert due[0] == 1

    def test_no_double_fire_across_windows(self):
        row = make_row(event_date="2020-09-18", notify_time="23:30")
        # First window catches it
        assert compute_due_reminder(row, utc(2025, 9, 17, 23, 31), utc(2025, 9, 17, 23, 29)) is not None
        # Subsequent window (last_check advanced) does not re-fire
        assert compute_due_reminder(row, utc(2025, 9, 17, 23, 33), utc(2025, 9, 17, 23, 31)) is None

    def test_timezone_respected(self):
        # 12:00 in Tokyo == 03:00 UTC
        row = make_row(timezone="Asia/Tokyo", notify_time="12:00", event_date="2020-09-17")
        now = utc(2025, 9, 17, 3, 0)
        assert compute_due_reminder(row, now, now - timedelta(minutes=1)) is not None
        noon_utc = utc(2025, 9, 17, 12, 0)
        assert compute_due_reminder(row, noon_utc, noon_utc - timedelta(minutes=1)) is None

    def test_one_time_passed_returns_none(self):
        row = make_row(event_date="2020-01-01", recurring=0)
        now = utc(2025, 9, 17, 12, 0)
        assert compute_due_reminder(row, now, now - timedelta(minutes=1)) is None

    def test_malformed_data_returns_none(self):
        now = utc(2025, 9, 17, 12, 0)
        last = now - timedelta(minutes=1)
        assert compute_due_reminder(make_row(notify_time="banana"), now, last) is None
        assert compute_due_reminder(make_row(notify_time="99:99"), now, last) is None
        assert compute_due_reminder(make_row(event_date="17-09-2020"), now, last) is None

    def test_unknown_timezone_falls_back_to_utc(self):
        row = make_row(timezone="Mars/Olympus", event_date="2020-09-17")
        now = utc(2025, 9, 17, 12, 0)
        assert compute_due_reminder(row, now, now - timedelta(minutes=1)) is not None

    def test_dst_spring_forward_does_not_crash(self):
        # 02:30 does not exist on 2025-03-09 in New York; must not crash,
        # and the slot must still fire somewhere near the gap.
        row = make_row(timezone="America/New_York", notify_time="02:30",
                       event_date="2020-03-09")
        fired = False
        last = utc(2025, 3, 9, 5, 0)  # 00:00 EST
        for _ in range(48):  # sweep 4 hours in 5-min windows
            now = last + timedelta(minutes=5)
            if compute_due_reminder(row, now, last):
                fired = True
                break
            last = now
        assert fired


class TestAllowedDays:
    def test_default(self):
        assert _allowed_days(make_row()) == DEFAULT_REMINDER_DAYS

    def test_custom(self):
        assert _allowed_days(make_row(reminder_days="7,0")) == [7, 0]

    def test_malformed_falls_back(self):
        assert _allowed_days(make_row(reminder_days="banana")) == DEFAULT_REMINDER_DAYS
