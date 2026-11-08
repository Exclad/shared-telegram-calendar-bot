# ❤️ Relationship Memory Bot

A private Telegram bot for couples to track important dates, share notes (text & photos), and calculate how long they have been together — with timezone-aware reminders.

## ✨ Features

- **📅 Events with reminders:** Add recurring (yearly) or one-time events. Get notified 1 week, 1 day, and on the day — or set a custom per-event schedule.
- **🗓 Inline calendar picker:** Pick dates from a tappable calendar, or type them in several formats (`17-09-2022`, `17/09/2022`, `2022-09-17`, `17 Sep 2022`).
- **⏰ Snooze:** Every reminder has a "Snooze 1h" button. Pending snoozes are lost if the bot restarts.
- **🌍 Timezone support:** Each chat sets its own timezone. Reminders fire at the correct local time.
- **📝 Notes with photos:** Save text notes or photos with captions. Photo notes have their own Edit/Delete buttons.
- **❤️ "Our Journey":** Calculates years, months, and days since a configurable event (anniversary, wedding, etc.). Tracks the event itself — renaming it doesn't break the link.
- **🔍 Upcoming & countdowns:** List events in the next 3 months, or `/days <name>` for a countdown to any event.
- **📤 Export / 📥 Import:** Text summary plus a JSON backup file, restorable with `/import`. Imports add items and restore the backed-up timezone and journey selection when present.
- **⌨️ Compact lists:** Numbered dates and full text notes keep long titles in the message. Notes are readable immediately; tap a number only to manage an item. Back returns to the full date list or the same note page.
- **📅 All dates at a glance:** List Dates shows every active date without pagination, sorted by its next occurrence. Friendly dates and Today / Tomorrow / in N days labels use the chat's timezone; original dates remain in the detail view. Long lists continue automatically in additional messages, with matching number buttons arranged five per row. Passed one-time events remain hidden.
- **⚙️ Simple main menu:** Four rows for everyday actions. Settings contains timezone, journey selection, and export. Save or Cancel returns to the item with Edit/Delete/Back controls; note edits retain the original list page.
- **📄 Note pagination:** Notes show 5 items per page. Full note pages that exceed Telegram's message limit continue automatically in additional messages, with controls below the final message.
- **🔒 Access control:** Only users in `ALLOWED_IDS` can interact with the bot; strangers are rate-limited.
- **🛡️ Gap-protected scheduler:** Missed reminders are caught up after brief downtime (up to a 2-hour window), including across midnight. Failed network sends are retried within that window without resending successful reminders.
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
- `DB_PATH` — Optional database path. Defaults to `dates.db`; Compose sets `/data/dates.db`.

## 🚀 How to Run

### Method 1: Local (for testing)

```bash
pip install -r requirements.txt
python main.py
```

### Method 2: Komodo Stack (recommended for the NAS)

The repository includes [compose.yaml](compose.yaml). It builds the Dockerfile on
its target server and rebuilds on deployment through `pull_policy: build`.
No published image or separate Komodo Build resource is needed. Use Docker Compose
2.27.1 or newer, on the same NAS Docker host that holds the existing data.

Build steps use the build host's network (`build.network: host`) with the
`network.host` build entitlement. This avoids the default build network, which
failed to resolve `pypi.org` on the NAS. With the normal local Docker builder,
dependency downloads use the NAS host network; a remote or containerized builder
uses its own host/network environment and may need separate configuration.

The running bot joins only your existing `allowed-internet` network. Host
networking is enabled only for the build, not for the deployed service. The NAS
Docker host must already have `allowed-internet`; Compose reuses it and does not
create or delete it. The build host still needs DNS and HTTPS access to
`pypi.org` and `files.pythonhosted.org`. Image pulls use the Docker daemon/builder
network separately.

If a build reports that `network.host` is not allowed, its BuildKit daemon must
also permit that entitlement. If PyPI still cannot resolve with host networking,
check DNS on the NAS/build host. A subsequent pip 'No matching distribution'
message after DNS failures does not establish that a pinned version is missing.

Create a **Stack** using a Git repository, with:

| Setting | Value |
| --- | --- |
| Server | Your NAS |
| Git provider | `github.com` |
| Repository | `Exclad/shared-telegram-calendar-bot` |
| Branch | `main` |
| Run directory | Repository root (`.`) |
| Compose file | `compose.yaml` |

In the Stack's **Environment**, set the absolute folder containing your current
`dates.db` and `.env`. For the previously recorded NAS folder:

```env
BOT_DATA_DIR=/volume2/docker/shared-telegram-calendar-bot
```

Confirm that this still matches the running bot's host data folder. Keep it
outside Komodo's Git checkout so repo refreshes cannot replace live data.
Your existing `.env` supplies `TELEGRAM_TOKEN` and `ALLOWED_IDS`; do not commit it.
Compose requires the data folder and `.env` to exist instead of creating a new
empty path when a mount path is wrong. No inbound ports are needed for polling.

**Before the first migration from the old container:**

1. Stop the old bot and disable its automatic restart. Run only one bot with this token.
2. Back up the live database and credentials. If the old container only mounts
   `/app/dates.db`, SQLite `dates.db-wal` and `dates.db-shm` files may be inside that
   container rather than on the NAS. Preserve the matching database and any
   sidecars from the stopped deployment before removing it. Resolve/checkpoint
   that database set before starting the replacement; never mix sidecars from a
   different database snapshot.
3. Ensure the existing database is in `BOT_DATA_DIR`. Do not replace it with an old
   local source-folder snapshot. The directory and database must be writable by
   uid 1000; the credentials file must be readable by uid 1000.
4. Deploy the Stack, then check logs, container health, List Dates and View Notes.

The entire data directory is mounted at `/data`, so future WAL/SHM sidecars stay
with the database across container replacements. The credentials file is also
mounted read-only at `/app/.env`. Existing deployments that do not set `DB_PATH`
continue using `/app/dates.db` with the image's default working directory.

For later code updates, deploy the Stack from the latest Git commit. Do not force
image pulling or use registry image-update checks for this locally built image.
Komodo's Stack documentation: https://komo.do/docs/deploy/compose

### Method 3: Docker Compose from a NAS shell

From a clone of this repository on the NAS, after the same migration precautions:

```sh
export BOT_DATA_DIR=/volume2/docker/shared-telegram-calendar-bot
docker compose config --quiet
docker compose up -d --build
```

The Dockerfile can also be used with a standalone Docker/Komodo Deployment.
Configure the same persistent data directory, credentials mount, and `DB_PATH`
there manually; a Compose file is only needed for the Stack/Compose approach.

The image includes a `HEALTHCHECK` that flags the container unhealthy if the
reminder loop hasn't run in the last 5 minutes.

The bot logs transient Telegram polling network retries at debug level and
warns if they persist, at most once every 5 minutes. A polling conflict warning
also appears at most once every 5 minutes with Telegram's error details; check
for another bot instance or a configured webhook. Network errors while handling
updates or reminder jobs remain visible, and unexpected errors remain errors.

View Notes shows full text with the original line breaks. Long notes and photo
captions continue automatically in text messages when they exceed Telegram's
limits; no extra tap is needed to read them. The full note is also included in Export.
Note content is saved and edited without an application-level character cutoff,
including whitespace and line breaks. Telegram still limits each submitted message.
Content already truncated by older versions cannot be recovered automatically.

## 📱 How to Use

1. Create a Telegram group with your partner, or use the bot in a private chat.
2. Add the bot to the group if using a group chat.
3. Type `/start` to see the main menu.
4. **Set your timezone** with ⚙️ Settings → 🌍 Set Timezone so reminders arrive at the correct local time.
5. **Add an event** with ➕ Add Date. Give it a name (e.g. "Anniversary"), pick a date from the calendar (or type it), a notification time, and choose recurring or one-time.
6. **Use ❤️ Our Journey** to see how long you've been together. By default it uses an event named "Anniversary" — customize this with ⚙️ Settings → ⚙️ Journey Event.

### Commands

| Command | Action |
|---------|--------|
| `/start` | Show the main menu |
| `/help` | Show help and all commands |
| `/settings` | Timezone, journey event, and export |
| `/add` | Add a new event |
| `/addnote` | Add a new note |
| `/upcoming` | Events in the next 3 months |
| `/days <name>` | Countdown to a specific event |
| `/export` | Export all data (text + JSON backup file) |
| `/import` | Restore events/notes from a JSON backup |
| `/timezone` | Set your timezone |
| `/journey` | Change journey event |
| `/editdate` | Edit any saved date by ID, including past one-time dates |
| `/editnote` | Edit any saved note by ID, including photo notes beyond the first 10 |
| `/delete` | Delete an item |
| `/cancel` | Cancel current operation |

### Reminder schedule

By default, the bot sends reminders at the configured notification time on these days:

| Days until event | Message |
|-----------------|---------|
| 7 | "Reminder: X is in 1 week." |
| 1 | "Get ready! X is TOMORROW!" |
| 0 | "Today is the day! Happy X!" (with a year count for anniversaries) |

**Custom schedules:** edit an event's **Reminders** field with comma-separated
day offsets, e.g. `30,7,1,0` (0 = on the day). Any offset works — `10` sends
"X is in 10 days." Existing custom schedules are kept when defaults change.

## 🧪 Tests

```bash
pip install pytest pytest-asyncio
python -m pytest tests/
```

Covers date parsing, DB operations and migrations, the reminder window logic
(including the midnight catch-up and DST edge cases), and an end-to-end
reminder run against a stub bot.

The `per_message=False` PTB warning is expected: the calendar and edit flows combine
callback buttons with typed replies. Dispatcher regression tests run entirely offline.

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
