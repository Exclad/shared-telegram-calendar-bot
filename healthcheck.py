"""Docker healthcheck: exit 0 if the reminder loop ran recently.

The scheduler stores its last check time in system_kv every 60s, so a stale
value (or unreadable database) means the bot is wedged.
"""

import sys
from datetime import datetime, timedelta, timezone

STALE_AFTER = timedelta(minutes=5)


def main() -> int:
    try:
        from db import get_system_setting
        from scheduler import LAST_CHECK_KEY

        val = get_system_setting(LAST_CHECK_KEY)
        if val is None:
            # Fresh start — scheduler hasn't ticked yet. Treat as healthy so
            # the container isn't killed during startup.
            return 0
        last = datetime.fromisoformat(val)
        if last.tzinfo is None:
            return 1
        if datetime.now(timezone.utc) - last > STALE_AFTER:
            print(f"Last reminder check too old: {last.isoformat()}", file=sys.stderr)
            return 1
        return 0
    except Exception as exc:  # any failure = unhealthy
        print(f"Healthcheck error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
