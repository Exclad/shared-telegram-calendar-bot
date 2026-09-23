from types import SimpleNamespace

import pytest
from telegram.error import Conflict, NetworkError

import handlers


@pytest.mark.asyncio
async def test_conflict_warning_is_rate_limited_and_actionable(monkeypatch, caplog):
    monkeypatch.setattr(handlers, "_last_conflict_warning", None)
    now = [1000.0]
    monkeypatch.setattr(handlers.time_mod, "monotonic", lambda: now[0])
    context = SimpleNamespace(error=Conflict("conflict"))

    await handlers.error_handler(None, context)
    now[0] += 10
    await handlers.error_handler(None, context)

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "another getUpdates poller" in warnings[0].message
    assert "conflict" in warnings[0].message

    now[0] += handlers.CONFLICT_WARNING_COOLDOWN
    await handlers.error_handler(None, context)
    assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 2


@pytest.mark.asyncio
async def test_polling_network_error_is_debug_only(caplog):
    caplog.set_level("DEBUG")
    handlers._polling_network_error_since = None
    handlers._last_polling_network_error = None
    handlers._last_polling_network_warning = None
    context = SimpleNamespace(error=NetworkError("temporary outage"), job=None)

    await handlers.error_handler(None, context)

    assert not [r for r in caplog.records if r.levelname == "WARNING"]
    assert any(r.levelname == "DEBUG" and "will retry" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_persistent_polling_network_error_warns_after_five_minutes(monkeypatch, caplog):
    handlers._polling_network_error_since = None
    handlers._last_polling_network_error = None
    handlers._last_polling_network_warning = None
    now = [1000.0]
    monkeypatch.setattr(handlers.time_mod, "monotonic", lambda: now[0])
    context = SimpleNamespace(error=NetworkError("connection timeout"), job=None)

    await handlers.error_handler(None, context)
    for _ in range(10):
        now[0] += 30
        await handlers.error_handler(None, context)

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "persisted for at least 300 seconds" in warnings[0].message

    for _ in range(9):
        now[0] += 30
        await handlers.error_handler(None, context)
    assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 1

    now[0] += 30
    await handlers.error_handler(None, context)
    assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 2


@pytest.mark.asyncio
async def test_polling_network_error_after_quiet_gap_starts_new_transient(monkeypatch, caplog):
    caplog.set_level("DEBUG")
    handlers._polling_network_error_since = None
    handlers._last_polling_network_error = None
    handlers._last_polling_network_warning = None
    now = [1000.0]
    monkeypatch.setattr(handlers.time_mod, "monotonic", lambda: now[0])
    context = SimpleNamespace(error=NetworkError("connection reset"), job=None)

    await handlers.error_handler(None, context)
    now[0] += handlers.POLLING_NETWORK_RECOVERY_GAP + 1
    await handlers.error_handler(None, context)

    assert not [r for r in caplog.records if r.levelname == "WARNING"]
    assert len([r for r in caplog.records if r.levelname == "DEBUG"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("update,job", [(object(), None), (None, object())])
async def test_network_error_during_update_or_job_remains_visible(update, job, caplog):
    context = SimpleNamespace(error=NetworkError("send failed"), job=job)

    await handlers.error_handler(update, context)

    assert any(r.levelname == "WARNING" and "processing an update or job" in r.message
               for r in caplog.records)
