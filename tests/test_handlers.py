from datetime import date, datetime, timezone as dt_timezone

from handlers import (
    _chunk_message,
    _is_passed_one_time,
    _next_event_date,
    _normalize_field_value,
    calculate_elapsed,
    TELEGRAM_MSG_LIMIT,
)


class TestCalculateElapsed:
    def test_exact_years(self):
        start = datetime(2020, 9, 17, tzinfo=dt_timezone.utc)
        today = datetime(2025, 9, 17, tzinfo=dt_timezone.utc)
        assert calculate_elapsed(start, today) == (5, 0, 0)

    def test_borrow_days(self):
        start = datetime(2020, 1, 31, tzinfo=dt_timezone.utc)
        today = datetime(2020, 3, 1, tzinfo=dt_timezone.utc)
        years, months, days = calculate_elapsed(start, today)
        assert years == 0
        assert months == 1
        assert days == 1  # Feb 2020 has 29 days

    def test_borrow_months(self):
        start = datetime(2020, 11, 1, tzinfo=dt_timezone.utc)
        today = datetime(2021, 2, 1, tzinfo=dt_timezone.utc)
        assert calculate_elapsed(start, today) == (0, 3, 0)


class TestNextEventDate:
    def test_recurring_this_year(self):
        ev = {"event_date": "2020-12-25", "recurring": 1}
        assert _next_event_date(ev, date(2025, 6, 1)) == date(2025, 12, 25)

    def test_recurring_next_year(self):
        ev = {"event_date": "2020-01-05", "recurring": 1}
        assert _next_event_date(ev, date(2025, 6, 1)) == date(2026, 1, 5)

    def test_one_time_future(self):
        ev = {"event_date": "2025-12-25", "recurring": 0}
        assert _next_event_date(ev, date(2025, 6, 1)) == date(2025, 12, 25)

    def test_one_time_passed(self):
        ev = {"event_date": "2020-12-25", "recurring": 0}
        assert _next_event_date(ev, date(2025, 6, 1)) is None

    def test_malformed(self):
        ev = {"event_date": "garbage", "recurring": 1}
        assert _next_event_date(ev, date(2025, 6, 1)) is None

    def test_leap_day_recurring(self):
        ev = {"event_date": "2020-02-29", "recurring": 1}
        assert _next_event_date(ev, date(2025, 2, 1)) == date(2025, 2, 28)


class TestIsPassedOneTime:
    def test_uses_provided_today(self):
        ev = {"event_date": "2025-06-01", "recurring": 0}
        assert _is_passed_one_time(ev, date(2025, 6, 2)) is True
        assert _is_passed_one_time(ev, date(2025, 6, 1)) is False
        assert _is_passed_one_time(ev, date(2025, 5, 31)) is False

    def test_recurring_never_passed(self):
        ev = {"event_date": "2000-01-01", "recurring": 1}
        assert _is_passed_one_time(ev, date(2025, 6, 2)) is False


class TestNormalizeFieldValue:
    def test_name_empty(self):
        error, _ = _normalize_field_value("Name", "   ")
        assert error

    def test_title_empty(self):
        error, _ = _normalize_field_value("Title", "")
        assert error

    def test_date_converted_to_iso(self):
        error, value = _normalize_field_value("Date", "17-09-2022")
        assert error is None
        assert value == "2022-09-17"

    def test_date_invalid(self):
        error, _ = _normalize_field_value("Date", "31-02-2023")
        assert error

    def test_time_normalized(self):
        error, value = _normalize_field_value("Time", "9:05")
        assert error is None
        assert value == "09:05"

    def test_recurring_garbage(self):
        error, _ = _normalize_field_value("Recurring", "maybe")
        assert error

    def test_reminders(self):
        error, value = _normalize_field_value("Reminders", "0, 7, 30")
        assert error is None
        assert value == "30,7,0"

    def test_reminders_invalid(self):
        error, _ = _normalize_field_value("Reminders", "soon")
        assert error

    def test_content_passthrough(self):
        error, value = _normalize_field_value("Content", "hello")
        assert error is None
        assert value == "hello"


class TestChunkMessage:
    def test_short_untouched(self):
        assert _chunk_message("hello") == ["hello"]

    def test_splits_on_lines(self):
        msg = "\n".join("x" * 100 for _ in range(60))  # ~6060 chars
        chunks = _chunk_message(msg)
        assert len(chunks) >= 2
        assert all(len(c) <= TELEGRAM_MSG_LIMIT for c in chunks)
        assert "".join(chunks).replace("\n", "") == msg.replace("\n", "")

    def test_pathological_single_line(self):
        msg = "x" * (TELEGRAM_MSG_LIMIT * 2 + 10)
        chunks = _chunk_message(msg)
        assert all(len(c) <= TELEGRAM_MSG_LIMIT for c in chunks)
        assert "".join(chunks) == msg
