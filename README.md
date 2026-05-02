# ❤️ Relationship Memory Bot

A private Telegram bot for couples to track important dates, share notes (text & photos), and calculate how long they have been together — with timezone-aware reminders.

## ✨ Features

- **📅 Events with reminders:** Add recurring (yearly) or one-time events. Get notified 1 month, 2-4 weeks, 1 day, and on the day.
- **🌍 Timezone support:** Each chat sets its own timezone. Reminders fire at the correct local time.
- **📝 Notes with photos:** Save text notes or photos with captions.
- **❤️ "Our Journey":** Calculates years, months, and days since a configurable event (anniversary, wedding, etc.).
- **🔍 Upcoming:** Lists all events in the next 3 months.
- **📤 Export:** Dumps all events and notes as a text message.
- **⌨️ Inline keyboards:** Edit or delete items directly from list views — no ID typing needed.
- **📄 Pagination:** Lists show 5 items per page with Prev/Next navigation.
- **🔒 Access control:** Only users in `ALLOWED_USERS` can interact with the bot.
- **🛡️ Gap-protected scheduler:** Missed reminders are caught up after brief downtime (up to 2-hour window).
- **💾 Persistent storage:** SQLite with WAL mode for concurrent access.

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

## 📱 How to Use

1. Create a Telegram group with your partner, or use the bot in a private chat.
2. Add the bot to the group if using a group chat.
3. Type `/start` to see the main menu.
4. **Set your timezone** with 🌍 Set Timezone so reminders arrive at the correct local time.
5. **Add an event** with ➕ Add Date. Give it a name (e.g. "Anniversary"), a date, a notification time, and choose recurring or one-time.
6. **Use ❤️ Our Journey** to see how long you've been together. By default it uses an event named "Anniversary" — customize this with ⚙️ Journey Event.

### Commands

| Command | Action |
|---------|--------|
| `/start` | Show the main menu |
| `/help` | Show help and all commands |
| `/add` | Add a new event |
| `/addnote` | Add a new note |
| `/upcoming` | Events in the next 3 months |
| `/export` | Export all data as text |
| `/timezone` | Set your timezone |
| `/journey` | Change journey event |
| `/delete` | Delete an item |
| `/cancel` | Cancel current operation |

### Reminder schedule

For each event, the bot sends reminders at the configured notification time on these days:

| Days until event | Message |
|-----------------|---------|
| 30 | "Head's up! X is in 1 month." |
| 28, 21, 14, 7 | "Reminder: X is in N week(s)." |
| 1 | "Get ready! X is TOMORROW!" |
| 0 | "Today is the day! Happy X!" |

## 💾 File Structure

```
.
├── main.py           # Entry point — builds and runs the bot
├── config.py         # Environment loading, constants, conversation states
├── db.py             # All database operations (parameterized queries, WAL, retry)
├── handlers.py       # Command, message, and callback handlers
├── keyboard.py       # Reply and inline keyboard layouts
├── scheduler.py      # Background reminder scheduler with gap protection
├── requirements.txt  # Python dependencies (pinned)
├── Dockerfile        # Docker image definition
├── .dockerignore     # Excludes .env and dates.db from build
└── .env              # Your bot token and allowed user IDs (NOT in git)
```
