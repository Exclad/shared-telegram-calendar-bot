# ❤️ Relationship Memory Bot

A private Telegram bot for couples to track important dates, share notes (text & photos), and calculate how long they have been together — with timezone-aware reminders.

## ✨ Features

- **📅 Events with reminders:** Add recurring (yearly) or one-time events. Get notified 1 month, 2-4 weeks, 1 day, and on the day — or set a custom per-event schedule.
- **🗓 Inline calendar picker:** Pick dates from a tappable calendar, or type them in several formats (`17-09-2022`, `17/09/2022`, `2022-09-17`, `17 Sep 2022`).
- **⏰ Snooze:** Every reminder has a "Snooze 1h" button.
- **🌍 Timezone support:** Each chat sets its own timezone. Reminders fire at the correct local time.
- **📝 Notes with photos:** Save text notes or photos with captions. Photo notes have their own Edit/Delete buttons.
- **❤️ "Our Journey":** Calculates years, months, and days since a configurable event (anniversary, wedding, etc.). Tracks the event itself — renaming it doesn't break the link.
- **🔍 Upcoming & countdowns:** List events in the next 3 months, or `/days <name>` for a countdown to any event.
- **📤 Export / 📥 Import:** Text summary plus a JSON backup file, restorable with `/import`.
- **⌨️ Inline keyboards:** Edit or delete items directly from list views — no ID typing needed.
- **📄 Pagination:** Lists show 5 items per page with Prev/Next navigation.
- **🔒 Access control:** Only users in `ALLOWED_IDS` can interact with the bot; strangers are rate-limited.
- **🛡️ Gap-protected scheduler:** Missed reminders are caught up after brief downtime (up to a 2-hour window), including across midnight.
- **💾 Persistent storage:** SQLite with WAL mode; dates stored as sortable ISO strings.

## 🛠️ Prerequisites

1. **A Telegram Bot Token** — Talk to [@BotFather](https://t.me/BotFather) to create a bot and get the API token.
2. **Docker** (for NAS/Server) or **Python 3.11+** (for local development).

## ⚙️ Configuration

Create a `.env` file:

```env
TELEGRAM_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
ALLOWED_IDS=123456789,987654321
```

- `TELEGRAM_TOKEN` — Your bot token from BotFather (required).
- `ALLOWED_IDS` — Comma-separated Telegram user IDs that can use the bot. If empty, no one can interact with it.

## 🚀 How to Run

### Method 1: Local (for testing)

```bash
pip install -r requirements.txt
python main.py
```

### Method 2: Docker / Portainer (NAS)

#### Build the image

```bash
docker build -t shared-telegram-calendar-bot:latest .
```

Or in Portainer: **Images → Build new** — point to the GitHub repo or upload the project folder.

#### Run the container

- **Image:** `shared-telegram-calendar-bot:latest`
- **Restart policy:** `Always`
- **Volumes** (bind mounts — critical for data persistence):
  - Host path: `/path/on/nas/.env` → Container path: `/app/.env`
  - Host path: `/path/on/nas/dates.db` → Container path: `/app/dates.db`

The `.env` and `dates.db` are excluded from the Docker image via `.dockerignore` — they must be bind-mounted at runtime.

> **Note:** If `dates.db` doesn't exist yet, the bot auto-creates it on first run.
>
> **Note:** The container runs as a non-root user (uid 1000). Make sure the
> bind-mounted `dates.db` (and its directory, for SQLite WAL sidecar files)
> is writable by uid 1000, e.g. `chown 1000 /path/on/nas/dates.db`.

The image includes a `HEALTHCHECK` that flags the container unhealthy if the
reminder loop hasn't run in the last 5 minutes.

## 📱 How to Use

1. Create a Telegram group with your partner, or use the bot in a private chat.
2. Add the bot to the group if using a group chat.
3. Type `/start` to see the main menu.
4. **Set your timezone** with 🌍 Set Timezone so reminders arrive at the correct local time.
5. **Add an event** with ➕ Add Date. Give it a name (e.g. "Anniversary"), pick a date from the calendar (or type it), a notification time, and choose recurring or one-time.
6. **Use ❤️ Our Journey** to see how long you've been together. By default it uses an event named "Anniversary" — customize this with ⚙️ Journey Event.

### Commands

| Command | Action |
|---------|--------|
| `/start` | Show the main menu |
| `/help` | Show help and all commands |
| `/add` | Add a new event |
| `/addnote` | Add a new note |
| `/upcoming` | Events in the next 3 months |
| `/days <name>` | Countdown to a specific event |
| `/export` | Export all data (text + JSON backup file) |
| `/import` | Restore events/notes from a JSON backup |
| `/timezone` | Set your timezone |
| `/journey` | Change journey event |
| `/delete` | Delete an item |
| `/cancel` | Cancel current operation |

### Reminder schedule

By default, the bot sends reminders at the configured notification time on these days:

| Days until event | Message |
|-----------------|---------|
| 30 | "Head's up! X is in 1 month." |
| 28, 21, 14, 7 | "Reminder: X is in N week(s)." |
| 1 | "Get ready! X is TOMORROW!" |
| 0 | "Today is the day! Happy X!" (with a year count for anniversaries) |

**Custom schedules:** edit an event's **Reminders** field with comma-separated
day offsets, e.g. `30,7,1,0` (0 = on the day). Any offset works — `10` sends
"X is in 10 days."

## 🧪 Tests

```bash
pip install pytest pytest-asyncio
python -m pytest tests/
```

Covers date parsing, DB operations and migrations, the reminder window logic
(including the midnight catch-up and DST edge cases), and an end-to-end
reminder run against a stub bot.

## 💾 File Structure

```
.
├── main.py           # Entry point — builds and runs the bot
├── config.py         # Environment loading, constants, conversation states
├── utils.py          # Date parsing/formatting helpers (ISO storage, display)
├── db.py             # All database operations (parameterized queries, WAL, migrations)
├── handlers.py       # Command, message, and callback handlers
├── keyboard.py       # Reply and inline keyboard layouts (incl. calendar picker)
├── scheduler.py      # Reminder logic with gap protection (runs on PTB JobQueue)
├── healthcheck.py    # Docker healthcheck — verifies the reminder loop is alive
├── tests/            # Pytest suite
├── requirements.txt  # Python dependencies (pinned)
├── Dockerfile        # Docker image (non-root, healthcheck)
├── .dockerignore     # Excludes .env and dates.db from build
└── .env              # Your bot token and allowed user IDs (NOT in git)
```
