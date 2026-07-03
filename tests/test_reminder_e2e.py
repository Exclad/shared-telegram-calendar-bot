"""End-to-end reminder check against a real database with a stub bot."""

from datetime import datetime, timedelta, timezone as dt_timezone

import pytest

import db
import scheduler
from scheduler import check_reminders, send_snoozed_reminder


class StubBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))


class StubJob:
    def __init__(self, data):
        self.data = data


class StubContext:
    def __init__(self, job_data=None):
        self.bot = StubBot()
        self.job = StubJob(job_data) if job_data else None


@pytest.mark.asyncio
async def test_check_reminders_sends_day_of_message(temp_db):
    now = datetime.now(dt_timezone.utc)
    # Event whose anniversary is today (5 years ago), notify time = now
    event_date = (now - timedelta(days=365 * 5 + 1)).date().replace(
        month=now.month, day=now.day
    )
    db.add_event(1, "Anniversary", event_date.isoformat(), now.strftime("%H:%M"))

    ctx = StubContext()
    await check_reminders(ctx)

    assert len(ctx.bot.sent) == 1
    chat_id, text, kwargs = ctx.bot.sent[0]
    assert chat_id == 1
    assert "Today is the day" in text
    assert kwargs["reply_markup"] is not None  # snooze button attached

    # Second run in the same minute must not double-send (last_check advanced)
    await check_reminders(ctx)
    assert len(ctx.bot.sent) == 1


@pytest.mark.asyncio
async def test_check_reminders_respects_custom_schedule(temp_db):
    now = datetime.now(dt_timezone.utc)
    target = (now + timedelta(days=3)).date()
    # 3 days out is not in the custom "7,0" schedule → nothing sent
    db.add_event(1, "Trip", target.isoformat(), now.strftime("%H:%M"),
                 recurring=False, reminder_days="7,0")

    ctx = StubContext()
    await check_reminders(ctx)
    assert ctx.bot.sent == []


@pytest.mark.asyncio
async def test_snoozed_reminder_resends(temp_db):
    now = datetime.now(dt_timezone.utc)
    eid = db.add_event(1, "Anniversary", now.date().replace(year=now.year - 2).isoformat(),
                       "12:00")
    ctx = StubContext(job_data={"chat_id": 1, "event_id": eid})
    await send_snoozed_reminder(ctx)
    assert len(ctx.bot.sent) == 1
    assert "Snoozed" in ctx.bot.sent[0][1]


@pytest.mark.asyncio
async def test_snoozed_reminder_skips_deleted_event(temp_db):
    ctx = StubContext(job_data={"chat_id": 1, "event_id": 999})
    await send_snoozed_reminder(ctx)
    assert ctx.bot.sent == []
