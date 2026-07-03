"""Smoke tests: application wiring and end-to-end flows that don't need Telegram."""

import json

import pytest
from telegram.ext import Application, ConversationHandler

import db
from handlers import register_handlers
from keyboard import build_calendar_inline


class TestApplicationWiring:
    def test_register_handlers_builds(self):
        app = Application.builder().token("123456:TEST-TOKEN").build()
        register_handlers(app)
        # group 0 conversations + group 1 globals both registered
        assert 0 in app.handlers and 1 in app.handlers
        conv_count = sum(isinstance(h, ConversationHandler) for h in app.handlers[0])
        assert conv_count == 8

    def test_job_queue_available(self):
        app = Application.builder().token("123456:TEST-TOKEN").build()
        assert app.job_queue is not None


class TestCalendarKeyboard:
    def test_month_layout(self):
        kb = build_calendar_inline(2025, 9)
        flat = [b for row in kb.inline_keyboard for b in row]
        picks = [b.callback_data for b in flat if b.callback_data.startswith("cal|pick|")]
        assert len(picks) == 30  # September has 30 days
        assert "cal|pick|2025|9|1" in picks
        assert "cal|pick|2025|9|30" in picks

    def test_nav_buttons(self):
        kb = build_calendar_inline(2025, 1)
        flat = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "cal|nav|2024|12" in flat  # prev month wraps year
        assert "cal|nav|2025|2" in flat


class TestExportImportRoundtrip:
    def test_backup_format_reimports(self, temp_db):
        db.add_event(1, "Anniversary", "2020-09-17", "12:00", recurring=True,
                     reminder_days="7,0")
        db.add_note(1, "Song", "Our song is X")
        db.add_note(1, "Pic", "caption", photo_id="PHOTO123")

        # Build backup exactly like export_data does
        backup = {
            "version": 1,
            "timezone": db.get_timezone(1),
            "events": [
                {
                    "name": ev["name"],
                    "date": ev["event_date"],
                    "notify_time": ev["notify_time"],
                    "recurring": bool(ev.get("recurring")),
                    "reminder_days": ev.get("reminder_days"),
                }
                for ev in db.get_events(1)
            ],
            "notes": [
                {"title": n["title"], "content": n["content"] or "", "photo_id": n["photo_id"]}
                for n in db.get_notes(1)
            ],
        }
        raw = json.dumps(backup)

        # Re-import into a different chat using the same parsing rules as import_receive
        data = json.loads(raw)
        from utils import parse_iso_date, parse_user_date, parse_time_hhmm, to_iso

        for ev in data["events"]:
            parsed = parse_iso_date(str(ev["date"])) or parse_user_date(str(ev["date"]))
            notify = parse_time_hhmm(str(ev["notify_time"])) or "12:00"
            db.add_event(2, str(ev["name"]).strip(), to_iso(parsed), notify,
                         recurring=bool(ev["recurring"]),
                         reminder_days=ev["reminder_days"])
        for n in data["notes"]:
            db.add_note(2, str(n["title"]).strip(), str(n["content"]),
                        n["photo_id"])

        restored_events = db.get_events(2)
        restored_notes = db.get_notes(2)
        assert len(restored_events) == 1
        assert restored_events[0]["event_date"] == "2020-09-17"
        assert restored_events[0]["reminder_days"] == "7,0"
        assert len(restored_notes) == 2
        assert restored_notes[1]["photo_id"] == "PHOTO123"
