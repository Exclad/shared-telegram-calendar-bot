import sqlite3

import pytest

import db


class TestMigration:
    def test_legacy_dates_converted_to_iso(self, temp_db):
        # Insert a legacy DD-MM-YYYY row directly
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "INSERT INTO events (chat_id, name, event_date, notify_time, recurring) "
            "VALUES (1, 'Anniversary', '17-09-2022', '12:00', 1)"
        )
        conn.commit()
        conn.close()

        db.migrate()

        ev = db.get_events(1)[0]
        assert ev["event_date"] == "2022-09-17"

    def test_legacy_journey_name_resolved_to_id(self, temp_db):
        event_id = db.add_event(1, "Wedding", "2020-06-01", "12:00")
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "INSERT INTO user_settings (chat_id, timezone, journey_event) "
            "VALUES (1, 'UTC', 'wedding')"
        )
        conn.commit()
        conn.close()

        db.migrate()

        date_iso, name = db.get_journey_event_for_chat(1)
        assert date_iso == "2020-06-01"
        assert name == "Wedding"
        # Verify it resolved by id, not name
        conn = sqlite3.connect(temp_db)
        row = conn.execute(
            "SELECT journey_event_id FROM user_settings WHERE chat_id = 1"
        ).fetchone()
        conn.close()
        assert row[0] == event_id

    def test_migrate_idempotent(self, temp_db):
        db.migrate()
        db.migrate()


class TestEvents:
    def test_add_and_get(self, temp_db):
        db.add_event(1, "Anniversary", "2022-09-17", "12:00", recurring=True)
        events = db.get_events(1)
        assert len(events) == 1
        assert events[0]["name"] == "Anniversary"
        assert events[0]["event_date"] == "2022-09-17"
        assert events[0]["recurring"] == 1

    def test_add_rejects_non_iso(self, temp_db):
        with pytest.raises(ValueError):
            db.add_event(1, "X", "17-09-2022", "12:00")

    def test_chat_isolation(self, temp_db):
        db.add_event(1, "Mine", "2022-09-17", "12:00")
        assert db.get_events(2) == []
        assert db.get_event(2, 1) is None
        assert db.delete_event(2, 1) is False
        assert db.get_events(1) != []

    def test_update_fields(self, temp_db):
        eid = db.add_event(1, "Old", "2022-09-17", "12:00")
        assert db.update_event(1, eid, "Name", "New")
        assert db.update_event(1, eid, "Date", "2023-01-01")
        assert db.update_event(1, eid, "Time", "9:05")
        assert db.update_event(1, eid, "Recurring", "no")
        assert db.update_event(1, eid, "Reminders", "7,1,0")
        ev = db.get_event(1, eid)
        assert ev["name"] == "New"
        assert ev["event_date"] == "2023-01-01"
        assert ev["notify_time"] == "09:05"
        assert ev["recurring"] == "0" or ev["recurring"] == 0 or ev["recurring"] == "0"
        assert ev["reminder_days"] == "7,1,0"

    def test_update_rejects_invalid(self, temp_db):
        eid = db.add_event(1, "E", "2022-09-17", "12:00")
        with pytest.raises(ValueError):
            db.update_event(1, eid, "Name", "   ")
        with pytest.raises(ValueError):
            db.update_event(1, eid, "Date", "17-09-2022")
        with pytest.raises(ValueError):
            db.update_event(1, eid, "Time", "25:00")
        with pytest.raises(ValueError):
            db.update_event(1, eid, "Recurring", "maybe")
        with pytest.raises(ValueError):
            db.update_event(1, eid, "Reminders", "banana")
        with pytest.raises(ValueError):
            db.update_event(1, eid, "Bogus", "x")

    def test_delete(self, temp_db):
        eid = db.add_event(1, "E", "2022-09-17", "12:00")
        assert db.delete_event(1, eid) is True
        assert db.delete_event(1, eid) is False


class TestNotes:
    def test_add_get_update_delete(self, temp_db):
        nid = db.add_note(1, "Title", "Content")
        note = db.get_note(1, nid)
        assert note["title"] == "Title"
        assert db.update_note(1, nid, "Content", "New content")
        assert db.get_note(1, nid)["content"] == "New content"
        assert db.delete_note(1, nid) is True

    def test_photo_replace(self, temp_db):
        nid = db.add_note(1, "Pic", "", photo_id="AAA")
        assert db.update_note(1, nid, "Content", "cap", photo_id="BBB")
        note = db.get_note(1, nid)
        assert note["photo_id"] == "BBB"
        assert note["content"] == "cap"

    def test_empty_title_rejected(self, temp_db):
        nid = db.add_note(1, "T", "c")
        with pytest.raises(ValueError):
            db.update_note(1, nid, "Title", "  ")


class TestJourney:
    def test_exact_match_not_prefix(self, temp_db):
        # Bug fix: "Anniversary Trip" must NOT match default "Anniversary"
        db.add_event(1, "Anniversary Trip", "2023-05-05", "12:00")
        date_iso, name = db.get_journey_event_for_chat(1)
        assert date_iso is None
        assert name == "Anniversary"

        db.add_event(1, "anniversary", "2020-01-15", "12:00")
        date_iso, name = db.get_journey_event_for_chat(1)
        assert date_iso == "2020-01-15"

    def test_set_by_id_survives_rename(self, temp_db):
        eid = db.add_event(1, "Wedding", "2021-07-07", "12:00")
        db.set_journey_event(1, eid, "Wedding")
        db.update_event(1, eid, "Name", "Big Wedding")
        date_iso, name = db.get_journey_event_for_chat(1)
        assert date_iso == "2021-07-07"
        assert name == "Big Wedding"

    def test_set_journey_upsert(self, temp_db):
        eid = db.add_event(1, "A", "2021-07-07", "12:00")
        db.set_journey_event(1, eid, "A")
        db.set_journey_event(1, eid, "A")  # no crash on second call
        db.set_timezone(1, "Asia/Tokyo")
        assert db.get_timezone(1) == "Asia/Tokyo"

    def test_deleted_journey_event_falls_back(self, temp_db):
        eid = db.add_event(1, "Wedding", "2021-07-07", "12:00")
        db.set_journey_event(1, eid, "Wedding")
        db.delete_event(1, eid)
        date_iso, name = db.get_journey_event_for_chat(1)
        assert date_iso is None
        assert name == "Wedding"


class TestSettings:
    def test_timezone_default(self, temp_db):
        assert db.get_timezone(999) == "UTC"

    def test_timezone_upsert(self, temp_db):
        db.set_timezone(1, "Europe/Berlin")
        db.set_timezone(1, "Asia/Tokyo")
        assert db.get_timezone(1) == "Asia/Tokyo"

    def test_reminder_join_includes_settings(self, temp_db):
        db.add_event(1, "E", "2022-09-17", "12:00")
        db.set_timezone(1, "Asia/Tokyo")
        rows = db.get_all_events_with_timezone()
        assert rows[0]["timezone"] == "Asia/Tokyo"
        assert "id" in rows[0]
        assert "reminder_days" in rows[0]
