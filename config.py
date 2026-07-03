"""Configuration and constants for the Relationship Memory Bot."""

import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not found in .env file.")

allowed_ids_str = os.getenv("ALLOWED_IDS", "")
ALLOWED_USERS = [int(x.strip()) for x in allowed_ids_str.split(",") if x.strip()]
if not ALLOWED_USERS:
    print("Warning: ALLOWED_IDS is empty. No one will be able to use the bot.")

# Conversation states
NAME, DATE, TIME, RECURRING = range(4)
NOTE_TITLE, NOTE_CONTENT = range(4, 6)
DELETE_CHOICE = 6
SET_TIMEZONE = 7
EDIT_SELECT_ID, EDIT_SELECT_FIELD, EDIT_NEW_VALUE = range(8, 11)
INLINE_AWAIT_FIELD, INLINE_AWAIT_VALUE = range(11, 13)
JOURNEY_EVENT_STATE = 13
IMPORT_FILE = 14

DB_PATH = "dates.db"
MAX_INPUT_LENGTH = 200
MAX_NOTE_CONTENT_LENGTH = 2000

# Default event name used for the "Our Journey" calculation
DEFAULT_JOURNEY_EVENT = "Anniversary"

# Default reminder offsets (days before the event) when an event has no
# custom schedule configured.
DEFAULT_REMINDER_DAYS = [30, 28, 21, 14, 7, 1, 0]

# Unauthorized users get at most one rejection reply per this many seconds.
UNAUTHORIZED_REPLY_COOLDOWN = 60

# Maximum accepted size for an imported backup file (bytes).
MAX_IMPORT_FILE_SIZE = 256 * 1024
