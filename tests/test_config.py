"""Check that the bot and healthcheck use the same configurable database."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("custom_path", [False, True])
def test_database_path_is_shared_by_storage_and_healthcheck(tmp_path, custom_path):
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.update(TELEGRAM_TOKEN="123456:TEST-TOKEN", ALLOWED_IDS="111,222",
               PYTHONPATH=str(repo_root))
    env.pop("DB_PATH", None)
    expected = tmp_path / "dates.db"
    if custom_path:
        expected = tmp_path / "persistent" / "dates.db"
        expected.parent.mkdir()
        env["DB_PATH"] = str(expected)
    script = '''
from datetime import datetime, timezone
from pathlib import Path
import config
import db
import healthcheck
db.init_db()
db.set_system_setting("last_reminder_check", datetime.now(timezone.utc).isoformat())
assert healthcheck.main() == 0
print(Path(config.DB_PATH).resolve())
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=tmp_path,
                            env=env, capture_output=True, text=True, check=True)
    assert Path(result.stdout.strip()) == expected
    assert expected.is_file()
    if custom_path:
        assert not (tmp_path / "dates.db").exists()
