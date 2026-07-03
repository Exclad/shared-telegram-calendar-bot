import os
import sys

# Config is imported at module load and requires these
os.environ.setdefault("TELEGRAM_TOKEN", "123456:TEST-TOKEN")
os.environ.setdefault("ALLOWED_IDS", "111,222")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import db as db_module


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point the db module at a fresh SQLite file and initialize the schema."""
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_module, "DB_PATH", path)
    db_module.init_db()
    db_module.migrate()
    return path
