from datetime import date

from utils import (
    format_display,
    parse_iso_date,
    parse_recurring,
    parse_reminder_days,
    parse_time_hhmm,
    parse_user_date,
    to_iso,
)


class TestParseUserDate:
    def test_ddmmyyyy(self):
        assert parse_user_date("17-09-2022") == date(2022, 9, 17)

    def test_slashes_and_dots(self):
        assert parse_user_date("17/09/2022") == date(2022, 9, 17)
        assert parse_user_date("17.09.2022") == date(2022, 9, 17)

    def test_iso(self):
        assert parse_user_date("2022-09-17") == date(2022, 9, 17)

    def test_natural(self):
        assert parse_user_date("17 Sep 2022") == date(2022, 9, 17)
        assert parse_user_date("17 September 2022") == date(2022, 9, 17)

    def test_whitespace_normalized(self):
        assert parse_user_date("  17   Sep   2022 ") == date(2022, 9, 17)

    def test_invalid(self):
        assert parse_user_date("31-02-2023") is None
        assert parse_user_date("not a date") is None
        assert parse_user_date("") is None
        assert parse_user_date(None) is None

    def test_leap_day(self):
        assert parse_user_date("29-02-2024") == date(2024, 2, 29)
        assert parse_user_date("29-02-2023") is None


class TestIsoHelpers:
    def test_roundtrip(self):
        d = date(2022, 9, 17)
        assert parse_iso_date(to_iso(d)) == d

    def test_parse_iso_invalid(self):
        assert parse_iso_date("17-09-2022") is None
        assert parse_iso_date(None) is None

    def test_format_display(self):
        assert format_display(date(2022, 9, 17)) == "17-09-2022"
        assert format_display("2022-09-17") == "17-09-2022"

    def test_format_display_bad_data_passthrough(self):
        assert format_display("garbage") == "garbage"


class TestParseTime:
    def test_valid(self):
        assert parse_time_hhmm("09:30") == "09:30"
        assert parse_time_hhmm("9:30") == "09:30"
        assert parse_time_hhmm("23:59") == "23:59"

    def test_invalid(self):
        assert parse_time_hhmm("24:00") is None
        assert parse_time_hhmm("12:60") is None
        assert parse_time_hhmm("noon") is None
        assert parse_time_hhmm("") is None


class TestParseReminderDays:
    def test_valid(self):
        assert parse_reminder_days("30,7,1,0") == [30, 7, 1, 0]

    def test_sorted_deduped(self):
        assert parse_reminder_days("1, 30, 1, 7") == [30, 7, 1]

    def test_invalid(self):
        assert parse_reminder_days("") is None
        assert parse_reminder_days("a,b") is None
        assert parse_reminder_days("-1") is None
        assert parse_reminder_days("400") is None
        assert parse_reminder_days(",".join(str(i) for i in range(25))) is None


class TestParseRecurring:
    def test_yes(self):
        for v in ("yes", "Yes (Recurring)", "Y", "true", "1", "recurring"):
            assert parse_recurring(v) is True

    def test_no(self):
        for v in ("no", "No (One-time)", "N", "false", "0", "one-time"):
            assert parse_recurring(v) is False

    def test_garbage(self):
        assert parse_recurring("maybe") is None
        assert parse_recurring("") is None
