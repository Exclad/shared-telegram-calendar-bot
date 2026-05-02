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
NAME, DATE, TIME = range(3)
NOTE_TITLE, NOTE_CONTENT = range(3, 5)
DELETE_CHOICE = 5
SET_TIMEZONE = 6
EDIT_SELECT_ID, EDIT_SELECT_FIELD, EDIT_NEW_VALUE = range(7, 10)

DB_PATH = "dates.db"
MAX_INPUT_LENGTH = 200
MAX_NOTE_CONTENT_LENGTH = 2000
