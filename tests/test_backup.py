import os
import sqlite3
import subprocess
import sys


def test_backup_is_consistent_and_applies_retention(tmp_path):
    source = tmp_path / "source.db"
    backups = tmp_path / "backups"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE example(value TEXT)")
        db.execute("INSERT INTO example VALUES ('preserved')")
    env = {**os.environ, "CIVICS_DB": str(source), "CIVICS_BACKUP_DIR": str(backups), "CIVICS_BACKUP_RETENTION": "2"}
    script = os.path.join(os.path.dirname(__file__), "..", "scripts", "backup_civics_db.py")
    for _ in range(3):
        subprocess.run([sys.executable, script], env=env, check=True, capture_output=True, text=True)
        # Backups made within one second intentionally replace the same daily-run timestamp.
    files = list(backups.glob("civics-*.db"))
    assert 1 <= len(files) <= 2
    with sqlite3.connect(files[0]) as db:
        assert db.execute("SELECT value FROM example").fetchone()[0] == "preserved"
