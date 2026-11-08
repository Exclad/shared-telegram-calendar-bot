"""Regression checks for upgrade flows; no Telegram network or live database."""

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from xml.etree import ElementTree

import pytest
import pytest_asyncio
from telegram import Update
from telegram.ext import Application
from telegram.request import BaseRequest

import db
import handlers
import healthcheck
import scheduler
from telegram.error import NetworkError
from scheduler import _days_and_years
from utils import parse_recurring, parse_reminder_days


class OfflineRequest(BaseRequest):
    """Exercise PTB dispatch and enforce Telegram's text/caption limits locally."""

    def __init__(self):
        self.calls = []

    @property
    def read_timeout(self):
        return 5

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        endpoint = url.rsplit("/", 1)[-1]
        params = request_data.parameters if request_data else {}
        self.calls.append((endpoint, params))
        if endpoint == "getMe":
            result = {"id": 999, "is_bot": True, "first_name": "Test", "username": "test_bot"}
        elif endpoint == "answerCallbackQuery":
            result = True
        else:
            assert endpoint in {
                "sendMessage", "sendPhoto", "editMessageText", "editMessageCaption",
                "editMessageReplyMarkup",
            }
            for field, limit in (("text", 4096), ("caption", 1024)):
                if field in params:
                    content = params[field]
                    if params.get("parse_mode") == "HTML":
                        content = "".join(ElementTree.fromstring(f"<root>{content}</root>").itertext())
                    assert len(content.encode("utf-16-le")) // 2 <= limit
            result = {
                "message_id": len(self.calls), "date": 0,
                "chat": {"id": params["chat_id"], "type": "private"},
                "text": params.get("text", ""),
            }
        return 200, json.dumps({"ok": True, "result": result}).encode()


@pytest_asyncio.fixture
async def offline_app(temp_db, monkeypatch):
    monkeypatch.setattr(handlers, "ALLOWED_USERS", [111, 222])
    request = OfflineRequest()
    app = (Application.builder().token("123456:TEST-TOKEN")
           .request(request).get_updates_request(OfflineRequest()).build())
    handlers.register_handlers(app)
    app.remove_error_handler(handlers.error_handler)
    errors = []

    async def capture_error(update, context):
        errors.append(context.error)

    app.add_error_handler(capture_error)
    async with app:
        yield app, request, errors


def message_update(app, text, chat_id=1, user_id=111):
    message = {
        "message_id": 1, "date": 0, "chat": {"id": chat_id, "type": "private"},
        "from": {"id": user_id, "first_name": "User", "is_bot": False}, "text": text,
    }
    if text.startswith("/"):
        message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    return Update.de_json({"update_id": 1, "message": message}, app.bot)


@pytest.mark.asyncio
async def test_switching_flows_cancels_the_previous_conversation(offline_app):
    app, request, errors = offline_app
    for text in ("/add", "/timezone"):
        await app.process_update(message_update(app, text))
    replies = [p["text"] for method, p in request.calls if method == "sendMessage"]
    assert "cancelled" in replies[-1]
    assert not errors


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["/start", "❓ Help", "📅 List Dates"])
async def test_global_menu_action_replies_once_and_ends_the_draft(offline_app, text):
    app, request, errors = offline_app
    await app.process_update(message_update(app, "/add"))
    before = len([method for method, _ in request.calls if method == "sendMessage"])
    await app.process_update(message_update(app, text))
    assert len([method for method, _ in request.calls if method == "sendMessage"]) == before + 1
    before = len(request.calls)
    await app.process_update(message_update(app, "orphaned name"))
    assert len(request.calls) == before
    assert not errors


@pytest.mark.asyncio
async def test_long_event_name_is_saved_and_confirmed_consistently(offline_app):
    app, request, errors = offline_app
    for text in ("/add", "x" * 4096, "2026-10-17", "12:00", "No"):
        await app.process_update(message_update(app, text))
    assert not errors
    assert db.get_events(1)[0]["name"] == "x" * 200
    assert "x" * 201 not in request.calls[-1][1]["text"]


@pytest.mark.asyncio
async def test_calendar_today_resolves_the_chat_date_when_pressed(offline_app, monkeypatch):
    app, request, errors = offline_app
    today = [date(2026, 1, 31)]
    monkeypatch.setattr(handlers, "_chat_today", lambda chat_id: today[0])
    for text in ("/add", "Trip"):
        await app.process_update(message_update(app, text))
    buttons = request.calls[-1][1]["reply_markup"]["inline_keyboard"]
    today_button = next(b for row in buttons for b in row if b["text"] == "Today")
    today[0] = date(2026, 2, 1)
    update = Update.de_json({
        "update_id": 1, "callback_query": {
            "id": "cb", "chat_instance": "test", "data": today_button["callback_data"],
            "from": {"id": 111, "is_bot": False, "first_name": "User"},
            "message": {"message_id": 10, "date": 0, "chat": {"id": 1, "type": "private"}},
        },
    }, app.bot)
    await app.process_update(update)
    assert not errors
    buttons = request.calls[-1][1]["reply_markup"]["inline_keyboard"]
    assert any(b["callback_data"] == "cal|pick|2026|2|1" for row in buttons for b in row)


@pytest.mark.asyncio
async def test_calendar_today_in_current_month_is_a_noop(monkeypatch):
    monkeypatch.setattr(handlers, "_chat_today", lambda chat_id: date(2026, 2, 1))
    query = SimpleNamespace(data="cal|today", answer=AsyncMock(), edit_message_reply_markup=AsyncMock(),
                            message=SimpleNamespace(reply_markup=handlers.build_calendar_inline(2026, 2)))
    update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=1))
    assert await handlers.calendar_callback(update, SimpleNamespace()) == handlers.DATE
    query.answer.assert_awaited_once()
    query.edit_message_reply_markup.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_in_another_chat_preserves_the_event_draft(offline_app):
    app, request, errors = offline_app
    for chat_id, text in ((1, "/add"), (1, "Dinner"), (2, "/addnote"), (2, "/cancel"),
                          (1, "2026-10-17"), (1, "12:00"), (1, "No")):
        await app.process_update(message_update(app, text, chat_id=chat_id))
    assert not errors
    assert db.get_events(1)[0]["name"] == "Dinner"
    assert db.get_events(2) == []


@pytest.mark.asyncio
async def test_long_notes_fit_list_and_photo_limits(offline_app):
    app, request, errors = offline_app
    for i in range(5):
        db.add_note(1, "😀" * 200, "😀" * 2000)
    db.add_note(1, "Photo", "<&" * 1000, "PHOTO123")
    await app.process_update(message_update(app, "📝 View Notes"))
    assert not errors
    assert any(method == "sendPhoto" for method, _ in request.calls)
    # Rendering must not truncate the stored notes.
    assert len(db.get_notes(1)[0]["content"]) == 2000


@pytest.mark.asyncio
async def test_editing_long_photo_content_uses_a_text_prompt(offline_app):
    app, request, errors = offline_app
    note_id = db.add_note(1, "Photo", "x" * 2000, "PHOTO123")
    for data in (f"edit|note|{note_id}", f"field|note|{note_id}|Content"):
        update = Update.de_json({
            "update_id": 1, "callback_query": {
                "id": "cb", "chat_instance": "test", "data": data,
                "from": {"id": 111, "is_bot": False, "first_name": "User"},
                "message": {
                    "message_id": 10, "date": 0, "chat": {"id": 1, "type": "private"},
                    "photo": [{"file_id": "PHOTO123", "file_unique_id": "p", "width": 1, "height": 1}],
                },
            },
        }, app.bot)
        await app.process_update(update)
    assert not errors
    assert any(method == "sendMessage" for method, _ in request.calls)


@pytest.mark.asyncio
async def test_stale_field_button_does_not_modify_current_edit():
    draft = {"inline_edit_type": "note", "inline_edit_id": 2}
    query = SimpleNamespace(data="field|note|1|Content", answer=AsyncMock())
    update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=1),
                             effective_user=SimpleNamespace(id=handlers.ALLOWED_USERS[0]))
    assert await handlers.inline_field_select(update, SimpleNamespace(user_data={1: draft})) == handlers.INLINE_AWAIT_FIELD
    assert "inline_edit_field" not in draft
    assert query.answer.call_args.kwargs["show_alert"] is True


def import_update(data):
    raw = json.dumps(data).encode()
    doc = SimpleNamespace(file_size=len(raw), get_file=AsyncMock(return_value=SimpleNamespace(
        download_as_bytearray=AsyncMock(return_value=bytearray(raw)))))
    return SimpleNamespace(message=SimpleNamespace(document=doc, reply_text=AsyncMock()),
                           effective_chat=SimpleNamespace(id=2))


@pytest.mark.asyncio
async def test_actual_export_import_restores_timezone_and_journey(temp_db):
    event_id = db.add_event(1, "Wedding", "2020-09-17", "09:00", reminder_days="7,0")
    db.set_timezone(1, "Asia/Singapore")
    db.set_journey_event(1, event_id, "Wedding")
    db.update_event(1, event_id, "Name", "Our Wedding")
    db.add_note(1, "Photo", "caption", "PHOTO123")
    message = SimpleNamespace(reply_text=AsyncMock(), reply_document=AsyncMock())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=handlers.ALLOWED_USERS[0]),
                             effective_chat=SimpleNamespace(id=1), message=message)
    await handlers.export_data(update, SimpleNamespace())
    backup_file = message.reply_document.call_args.kwargs["document"]
    imported = import_update(json.loads(backup_file.getvalue()))
    await handlers.import_receive(imported, SimpleNamespace())
    assert db.get_timezone(2) == "Asia/Singapore"
    assert db.get_journey_event_for_chat(2) == ("2020-09-17", "Our Wedding")
    assert db.get_journey_event(1) == db.get_journey_event(2) == "Our Wedding"
    assert db.get_events(2)[0]["reminder_days"] == "7,0"
    assert db.get_notes(2)[0]["photo_id"] == "PHOTO123"


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [{"events": None}, {"events": [], "notes": 5}])
async def test_invalid_backup_shape_is_rejected_before_writing(temp_db, data):
    update = import_update(data)
    assert await handlers.import_receive(update, SimpleNamespace()) == handlers.IMPORT_FILE
    assert db.get_events(2) == []
    assert db.get_notes(2) == []


@pytest.mark.asyncio
async def test_invalid_backup_items_are_skipped(temp_db):
    update = import_update({"events": [None, [], {"name": "Trip", "date": "2026-10-17"}],
                            "notes": [False, {}, {"title": "Good", "content": "OK"}]})
    await handlers.import_receive(update, SimpleNamespace())
    assert len(db.get_events(2)) == len(db.get_notes(2)) == 1
    assert "Skipped <b>4</b>" in update.message.reply_text.call_args.args[0]


@pytest.mark.parametrize("value", ["--1", "²", "9" * 5000], ids=["signs", "superscript", "huge"])
def test_malformed_reminder_offsets_do_not_raise(value):
    assert parse_reminder_days(value) is None


@pytest.mark.parametrize("value", ["yesterday", "nonsense", "not sure"])
def test_recurring_answers_are_strict(value):
    assert parse_recurring(value) is None


def test_future_recurring_event_is_not_projected_before_its_start():
    event = {"event_date": "2028-10-17", "recurring": 1}
    today = date(2026, 10, 10)
    assert handlers._next_event_date(event, today) == date(2028, 10, 17)
    assert _days_and_years(date(2028, 10, 17), True, today) == (738, None)


@pytest.mark.parametrize("start,today,expected", [
    ((2025, 1, 31), (2025, 2, 28), (0, 1, 0)),
    ((2024, 2, 29), (2025, 2, 28), (1, 0, 0)),
])
def test_journey_clamps_month_end(start, today, expected):
    assert handlers.calculate_elapsed(datetime(*start), datetime(*today)) == expected


@pytest.mark.asyncio
async def test_journey_uses_chat_date(offline_app, monkeypatch):
    app, request, errors = offline_app
    db.add_event(1, "Anniversary", "2026-10-04", "12:00")
    monkeypatch.setattr(handlers, "_chat_today", lambda chat_id: date(2026, 10, 5))
    await app.process_update(message_update(app, "❤️ Our Journey"))
    assert not errors
    text = request.calls[-1][1]["text"]
    assert "<b>1</b> days of love" in text


def test_healthcheck_without_scheduler_heartbeat_is_unhealthy(temp_db):
    assert healthcheck.main() == 1


def test_export_chunks_do_not_split_html_entities():
    message = "prefix " + "&amp;" * 1000 + "&#x27;" * 1000
    for chunk in handlers._chunk_message(message):
        ElementTree.fromstring(f"<root>{chunk}</root>")


def test_chunks_balance_multiline_html_and_preserve_emoji():
    text = "<b>" + "😀\n" * 3000 + "</b>"
    decoded = []
    for chunk in handlers._chunk_message(text):
        decoded.append("".join(ElementTree.fromstring(f"<root>{chunk}</root>").itertext()))
        assert len(chunk.encode("utf-16-le")) // 2 <= handlers.TELEGRAM_MSG_LIMIT
    assert "".join(decoded) == "😀\n" * 3000


@pytest.mark.asyncio
async def test_large_upcoming_countdown_and_edit_delete_lists(offline_app, monkeypatch):
    app, request, errors = offline_app
    monkeypatch.setattr(handlers, "_chat_today", lambda chat_id: date(2026, 10, 4))
    for i in range(40):
        db.add_event(1, "Trip\n" + "x" * 195, "2026-10-17", "12:00", recurring=False)
    for text in ("/upcoming", "/days Trip", "✏️ Edit Date", "/cancel", "/journey", "missing",
                 "/cancel", "/delete", "Delete Date"):
        await app.process_update(message_update(app, text))
    assert not errors


@pytest.mark.asyncio
async def test_failed_reminder_is_retried_without_resending_successes(temp_db, monkeypatch):
    clock = [datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)]

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz)

    monkeypatch.setattr(scheduler, "datetime", FrozenDatetime)
    for chat_id in (1, 2):
        db.add_event(chat_id, "Trip", "2026-10-04", "12:00", recurring=False)
    db.set_system_setting(scheduler.LAST_CHECK_KEY, "2026-10-04T11:59:00+00:00")
    delivered = []
    failed_once = [False]

    async def send_message(chat_id, **kwargs):
        if chat_id == 2 and not failed_once[0]:
            failed_once[0] = True
            raise NetworkError("temporary send failure")
        delivered.append(chat_id)

    context = SimpleNamespace(bot=SimpleNamespace(send_message=send_message))
    await scheduler.check_reminders(context)
    clock[0] = datetime(2026, 10, 4, 12, 1, tzinfo=timezone.utc)
    await scheduler.check_reminders(context)
    clock[0] = datetime(2026, 10, 4, 12, 2, tzinfo=timezone.utc)
    await scheduler.check_reminders(context)
    assert delivered == [1, 2]


@pytest.mark.asyncio
async def test_failed_event_fetch_preserves_catchup_window(temp_db, monkeypatch):
    old_check = "2026-10-04T11:59:00+00:00"
    db.set_system_setting(scheduler.LAST_CHECK_KEY, old_check)

    def unavailable():
        raise RuntimeError("temporary database failure")

    monkeypatch.setattr(scheduler, "get_all_events_with_timezone", unavailable)
    await scheduler.check_reminders(SimpleNamespace())
    assert db.get_system_setting(scheduler.LAST_CHECK_KEY) == old_check


# Compact list browsing and detail actions.
def callback_update(app, data, chat_id=1, user_id=111):
    return Update.de_json({
        "update_id": 1, "callback_query": {
            "id": "cb", "chat_instance": "test", "data": data,
            "from": {"id": user_id, "is_bot": False, "first_name": "User"},
            "message": {"message_id": 10, "date": 0, "chat": {"id": chat_id, "type": "private"}},
        },
    }, app.bot)


def visible_text(params):
    return "".join(ElementTree.fromstring(f"<root>{params['text']}</root>").itertext())


def inline_buttons(params):
    return [b for row in params["reply_markup"]["inline_keyboard"] for b in row]


@pytest.mark.asyncio
@pytest.mark.parametrize("item_type,menu,prefix", [
    ("event", "📅 List Dates", "ev"), ("note", "📝 View Notes", "nt"),
])
async def test_numbered_lists_open_the_correct_item_and_return_to_the_same_page(
    offline_app, item_type, menu, prefix,
):
    app, request, errors = offline_app
    ids = []
    for i in range(6):
        name = f"Very long shared title <&> {i} " + "😀" * 50
        ids.append(db.add_event(1, name, "2020-09-17", "09:30") if item_type == "event"
                   else db.add_note(1, name, "First line\nSecond line"))
    await app.process_update(message_update(app, menu))
    page = request.calls[-1][1]
    assert [b["text"] for b in page["reply_markup"]["inline_keyboard"][0]] == ["1", "2", "3", "4", "5"]
    assert "Very long shared title <&>" in visible_text(page)
    assert all(not b["callback_data"].startswith(("edit|", "del|")) for b in inline_buttons(page))
    assert all(len(b["callback_data"].encode()) <= 64 for b in inline_buttons(page))
    await app.process_update(callback_update(app, f"pg|{prefix}|2"))
    page = request.calls[-1][1]
    select = page["reply_markup"]["inline_keyboard"][0][0]
    selected_id = int(select["callback_data"].split("|")[2])
    assert selected_id in ids
    item = db.get_event(1, selected_id) if item_type == "event" else db.get_note(1, selected_id)
    name = item["name"] if item_type == "event" else item["title"]
    assert f"1. {name}" in visible_text(page)
    await app.process_update(callback_update(app, select["callback_data"]))
    detail = request.calls[-1][1]
    assert name in visible_text(detail)
    back = next(b for b in inline_buttons(detail) if b["callback_data"].startswith("pg|"))
    assert back["callback_data"] == f"pg|{prefix}|2"
    await app.process_update(callback_update(app, back["callback_data"]))
    assert request.calls[-1][1]["text"] == page["text"]
    await app.process_update(callback_update(app, select["callback_data"]))
    edit = next(b for b in inline_buttons(request.calls[-1][1]) if b["callback_data"].startswith("edit|"))
    await app.process_update(callback_update(app, edit["callback_data"]))
    field = "Name" if item_type == "event" else "Title"
    await app.process_update(callback_update(app, f"field|{item_type}|{selected_id}|{field}"))
    await app.process_update(message_update(app, "Renamed"))
    item = db.get_event(1, selected_id) if item_type == "event" else db.get_note(1, selected_id)
    assert item["name" if item_type == "event" else "title"] == "Renamed"
    assert not errors


@pytest.mark.asyncio
@pytest.mark.parametrize("item_type,prefix", [("event", "ev"), ("note", "nt")])
async def test_detail_delete_cancel_and_return_after_page_disappears(offline_app, item_type, prefix):
    app, request, errors = offline_app
    for i in range(6):
        if item_type == "event":
            db.add_event(1, f"Date {i}", "2020-09-17", "00:00")
        else:
            db.add_note(1, f"Note {i}", "Body")
    await app.process_update(callback_update(app, f"pg|{prefix}|2"))
    select = request.calls[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    item_id = int(select.split("|")[2])
    await app.process_update(callback_update(app, select))
    detail = request.calls[-1][1]["text"]
    delete = next(b["callback_data"] for b in inline_buttons(request.calls[-1][1]) if b["callback_data"].startswith("del|"))
    await app.process_update(callback_update(app, delete))
    confirm = request.calls[-1][1]
    assert "cannot be undone" in confirm["text"]
    item = db.get_event(1, item_id) if item_type == "event" else db.get_note(1, item_id)
    assert item["name" if item_type == "event" else "title"] in confirm["text"]
    cancel = next(b["callback_data"] for b in inline_buttons(confirm) if b["callback_data"].startswith("view|"))
    await app.process_update(callback_update(app, cancel))
    assert request.calls[-1][1]["text"] == detail
    await app.process_update(callback_update(app, delete))
    confirm = next(b["callback_data"] for b in inline_buttons(request.calls[-1][1]) if b["callback_data"].startswith("confirm_del|"))
    await app.process_update(callback_update(app, confirm))
    assert (db.get_event(1, item_id) if item_type == "event" else db.get_note(1, item_id)) is None
    back = inline_buttons(request.calls[-1][1])[0]["callback_data"]
    await app.process_update(callback_update(app, back))
    page = request.calls[-1][1]
    assert "1/1" not in page["text"]
    assert len(page["reply_markup"]["inline_keyboard"]) == 1
    assert all(b["callback_data"].endswith("|1") for b in inline_buttons(page))
    items = db.get_events(1) if item_type == "event" else db.get_notes(1)
    for item in items:
        (db.delete_event if item_type == "event" else db.delete_note)(1, item["id"])
    await app.process_update(callback_update(app, back))
    empty_message = "No active dates" if item_type == "event" else "No text notes"
    assert empty_message in request.calls[-1][1]["text"]
    assert not errors


@pytest.mark.asyncio
async def test_full_note_is_sent_automatically_and_detail_keeps_every_part(offline_app):
    app, request, errors = offline_app
    title, content = "😀" * 200, "<&>😀\n" * 400
    note_id = db.add_note(1, title, content)
    stored = db.get_note(1, note_id)
    await app.process_update(message_update(app, "📝 View Notes"))
    messages = [p for method, p in request.calls if method == "sendMessage"]
    assert len(messages) > 1
    displayed = "".join(visible_text(p) for p in messages)
    assert stored["content"] in displayed
    assert f"1. {stored['title']}" in displayed
    assert "…" not in displayed
    assert all(not p.get("reply_markup") for p in messages[:-1])
    assert inline_buttons(messages[-1])[0]["callback_data"] == f"view|note|{note_id}|1"
    await app.process_update(callback_update(app, f"view|note|{note_id}|1"))
    texts = []
    while True:
        detail = request.calls[-1][1]
        texts.append(visible_text(detail))
        next_part = next((b for b in inline_buttons(detail) if b["text"] == "Next ›"), None)
        if next_part is None:
            break
        assert len(texts) < 20
        await app.process_update(callback_update(app, next_part["callback_data"]))
    assert len(texts) > 1
    assert "".join(texts) == f"📝 {stored['title']}\n\n{stored['content']}"
    assert db.get_note(1, note_id) == stored
    assert not errors


@pytest.mark.asyncio
@pytest.mark.parametrize("item_type", ["event", "note"])
async def test_detail_cannot_read_another_chat_or_a_deleted_item(offline_app, item_type):
    app, request, errors = offline_app
    item_id = (db.add_event(2, "Private title", "2020-01-01", "00:00") if item_type == "event"
               else db.add_note(2, "Private title", "Secret body"))
    for chat_id in (1, 2):
        if chat_id == 2:
            (db.delete_event if item_type == "event" else db.delete_note)(2, item_id)
        await app.process_update(callback_update(app, f"view|{item_type}|{item_id}|1", chat_id=chat_id))
        assert "no longer available" in request.calls[-1][1]["text"]
        assert "Private" not in request.calls[-1][1]["text"]
    assert not errors


@pytest.mark.asyncio
async def test_browsing_from_an_old_button_ends_the_current_edit(offline_app):
    app, request, errors = offline_app
    note_id = db.add_note(1, "Title", "Original")
    for data in (f"edit|note|{note_id}", f"field|note|{note_id}|Content", f"view|note|{note_id}|1"):
        await app.process_update(callback_update(app, data))
    await app.process_update(message_update(app, "Should not be saved"))
    assert db.get_note(1, note_id)["content"] == "Original"
    assert not errors


@pytest.mark.asyncio
async def test_full_note_page_navigation_sends_all_chunks_with_controls_at_the_end(offline_app):
    app, request, errors = offline_app
    for i in range(11):
        db.add_note(1, f"Note {i}", f"Opening {i}\n\n" + "😀<&>" * 350 + f"\nEnding {i}")
    expected = db.get_notes(1)[5:10]
    await app.process_update(message_update(app, "📝 View Notes"))
    before = len(request.calls)
    await app.process_update(callback_update(app, "pg|nt|2"))
    messages = [(method, p) for method, p in request.calls[before:]
                if method in ("editMessageText", "sendMessage")]
    assert messages[0][0] == "editMessageText"
    assert len(messages) > 1
    displayed = "".join(visible_text(p) for _, p in messages)
    for number, note in enumerate(expected, 1):
        assert f"{number}. {note['title']}\n{note['content']}" in displayed
    assert all(not p.get("reply_markup") for _, p in messages[:-1])
    buttons = inline_buttons(messages[-1][1])
    assert [b["callback_data"] for b in buttons if b["callback_data"].startswith("view|")] == [
        f"view|note|{note['id']}|2" for note in expected
    ]
    assert any(b["callback_data"] == "pg|nt|3" for b in buttons)
    # The controls still open the correct note after the page was split.
    await app.process_update(callback_update(app, buttons[0]["callback_data"]))
    assert expected[0]["title"] in visible_text(request.calls[-1][1])
    assert not errors


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["Short caption\nSecond line", "<&>😀\n" * 400])
async def test_photo_note_displays_its_full_caption_without_an_extra_tap(offline_app, content):
    app, request, errors = offline_app
    note_id = db.add_note(1, "Photo note", content, "PHOTO123")
    stored = db.get_note(1, note_id)
    await app.process_update(message_update(app, "📝 View Notes"))
    photos = [p for method, p in request.calls if method == "sendPhoto"]
    messages = [p for method, p in request.calls if method == "sendMessage"]
    assert len(photos) == 1
    if messages:
        displayed = "".join(visible_text(p) for p in messages)
        assert not photos[0].get("reply_markup")
        assert all(not p.get("reply_markup") for p in messages[:-1])
        controls = messages[-1]
    else:
        displayed = visible_text({"text": photos[0]["caption"]})
        controls = photos[0]
    assert stored["content"] in displayed
    assert "…" not in displayed
    assert any(b["callback_data"] == f"edit|note|{note_id}" for b in inline_buttons(controls))
    assert not errors
