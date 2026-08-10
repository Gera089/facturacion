from datetime import datetime, timezone
from functools import wraps

import time
from datetime import datetime, timezone
from functools import wraps

import sqlite3

from app.core.config import settings
from app.legacy_db import get_legacy_connection


def _requires_mysql(fn):
    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        if not self._check_mysql():
            return {"ok": False, "error": "MySQL no disponible. Verifica conexion."}
        return fn(self, *args, **kwargs)

    return wrapper


def _get_sqlite():
    """Open a direct SQLite connection (not context-managed) for migration use."""
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(settings.db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _update_status(module_key: str, status: str, notes: str = ""):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = _get_sqlite()
    try:
        conn.execute(
            """
            UPDATE migration_notes
            SET status = ?, notes = ?, updated_at = ?
            WHERE module_key = ?
            """,
            (status, notes, now, module_key),
        )
        conn.commit()
    finally:
        conn.close()


class Migration:
    key: str = ""
    title: str = ""
    description: str = ""

    def _check_mysql(self) -> bool:
        try:
            conn = get_legacy_connection()
            conn.close()
            return True
        except Exception:
            return False

    def _get_mysql_cursor(self):
        if not self._check_mysql():
            raise RuntimeError("MySQL no disponible.Verifica conexion.")
        conn = get_legacy_connection()
        return conn, conn.cursor(dictionary=True)

    def preview(self) -> dict:
        raise NotImplementedError

    def run(self) -> dict:
        raise NotImplementedError
