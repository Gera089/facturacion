import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from app.core.config import settings


BOOTSTRAP_COMPANIES = [
    "EZA2007",
    "Gourmet España",
    "Ibersur",
    "Alimentos Europeos",
    "Aldeu",
    "Remision",
]


def _ensure_storage() -> None:
    settings.storage_dir.mkdir(parents=True, exist_ok=True)


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


@contextmanager
def get_connection():
    _ensure_storage()
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    _ensure_storage()
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                code TEXT NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS migration_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module_key TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
            """
        )

        now = datetime.now().isoformat(timespec="seconds")

        user_exists = conn.execute("SELECT id FROM users WHERE username = ?", ("admin",)).fetchone()
        if not user_exists:
            conn.execute(
                """
                INSERT INTO users (username, full_name, password_hash, role, active, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                ("admin", "Administrador Migracion", _hash_password("admin123"), "admin", now),
            )

        for company in BOOTSTRAP_COMPANIES:
            code = company.upper().replace(" ", "_").replace("Ñ", "N")
            exists = conn.execute("SELECT id FROM companies WHERE name = ?", (company,)).fetchone()
            if not exists:
                conn.execute(
                    """
                    INSERT INTO companies (name, code, active, created_at)
                    VALUES (?, ?, 1, ?)
                    """,
                    (company, code, now),
                )

        bootstrap_modules = [
            ("auth", "Autenticacion", "base"),
            ("clientes", "Clientes", "pendiente"),
            ("productos", "Productos", "pendiente"),
            ("facturacion", "Facturacion", "pendiente"),
            ("impresion", "Impresion", "pendiente"),
            ("cobranza", "Cobranza", "pendiente"),
            ("timbrado", "Timbrado", "pendiente"),
            ("reportes", "Reportes", "pendiente"),
            ("cadenas", "Cadenas", "listo"),
        ]
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conciliaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_id TEXT,
                cliente_nombre TEXT,
                monto_pago REAL DEFAULT 0,
                fecha TEXT,
                notas TEXT DEFAULT '',
                created_at TEXT,
                created_by INTEGER,
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conciliacion_partidas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conciliacion_id INTEGER NOT NULL,
                factura_folio TEXT,
                factura_id TEXT,
                monto_factura REAL DEFAULT 0,
                comision REAL DEFAULT 0,
                iva REAL DEFAULT 0,
                total REAL DEFAULT 0,
                envio REAL DEFAULT 0,
                producto_no_enviado REAL DEFAULT 0,
                total_envio REAL DEFAULT 0,
                pago REAL DEFAULT 0,
                documento_nombre TEXT DEFAULT '',
                documento_monto REAL DEFAULT 0,
                FOREIGN KEY(conciliacion_id) REFERENCES conciliaciones(id)
            )
        """)
        for col in ("iva", "envio", "producto_no_enviado", "total_envio", "pago"):
            try:
                conn.execute(f"ALTER TABLE conciliacion_partidas ADD COLUMN {col} REAL DEFAULT 0")
            except Exception:
                pass
        try:
            conn.execute("ALTER TABLE conciliaciones ADD COLUMN amazon_data TEXT DEFAULT ''")
        except Exception:
            pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conciliacion_conceptos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conciliacion_id INTEGER NOT NULL,
                nombre TEXT DEFAULT '',
                descripcion TEXT DEFAULT '',
                monto REAL DEFAULT 0,
                FOREIGN KEY(conciliacion_id) REFERENCES conciliaciones(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conciliacion_visibilidad (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conciliacion_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                FOREIGN KEY(conciliacion_id) REFERENCES conciliaciones(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                detail TEXT DEFAULT '',
                ip_address TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_blocklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                blocked_by INTEGER NOT NULL,
                reason TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(blocked_by) REFERENCES users(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_section_permissions (
                username TEXT PRIMARY KEY,
                sections_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            )
        """)

        for module_key, title, status in bootstrap_modules:
            exists = conn.execute(
                "SELECT id FROM migration_notes WHERE module_key = ?",
                (module_key,),
            ).fetchone()
            if not exists:
                conn.execute(
                    """
                    INSERT INTO migration_notes (module_key, title, status, notes, updated_at)
                    VALUES (?, ?, ?, '', ?)
                    """,
                    (module_key, title, status, now),
                )


def authenticate_user(username: str, password: str):
    password_hash = _hash_password(password)
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, username, full_name, role, active
            FROM users
            WHERE username = ? AND password_hash = ? AND active = 1
            """,
            (username.strip(), password_hash),
        ).fetchone()
        return dict(row) if row else None


def ensure_user(username: str, full_name: str, role: str) -> dict:
    """
    Upsert a user into the local SQLite users table (synced from MySQL).
    Returns the user dict with id, username, full_name, role.
    """
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (username.strip(),),
        ).fetchone()
        if existing:
            # Un usuario bloqueado no debe reactivarse automáticamente al
            # sincronizarse de MySQL durante un nuevo inicio de sesión.
            is_blocked = conn.execute(
                "SELECT 1 FROM user_blocklist WHERE user_id = ?",
                (existing["id"],),
            ).fetchone() is not None
            conn.execute(
                "UPDATE users SET full_name = ?, role = ?, active = ? WHERE id = ?",
                (full_name, role, 0 if is_blocked else 1, existing["id"]),
            )
            user_id = existing["id"]
        else:
            cur = conn.execute(
                """
                INSERT INTO users (username, full_name, password_hash, role, active, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (username.strip(), full_name, _hash_password("__mysql_synced__"), role, now),
            )
            user_id = cur.lastrowid
        return {
            "id": user_id,
            "username": username.strip(),
            "full_name": full_name,
            "role": role,
        }


def get_user_sections(username: str):
    """None conserva los permisos normales del perfil; una lista limita secciones."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT sections_json FROM user_section_permissions WHERE username = ?",
            (str(username or "").strip(),),
        ).fetchone()
        if not row:
            return None
        try:
            value = json.loads(row["sections_json"] or "[]")
        except Exception:
            value = []
        return value if isinstance(value, list) else []


def set_user_sections(username: str, sections: list[str]) -> None:
    clean = sorted({str(item or "").strip() for item in (sections or []) if str(item or "").strip()})
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO user_section_permissions (username, sections_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                sections_json = excluded.sections_json,
                updated_at = excluded.updated_at
            """,
            (str(username or "").strip(), json.dumps(clean), datetime.now().isoformat(timespec="seconds")),
        )


def delete_user_sections(username: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM user_section_permissions WHERE username = ?", (str(username or "").strip(),))


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    created_at = datetime.now().isoformat(timespec="seconds")
    expires_at = datetime.now().replace(year=datetime.now().year + 1).isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO sessions (token, user_id, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (token, user_id, created_at, expires_at),
        )
    return token


def get_user_by_token(token: str):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT u.id, u.username, u.full_name, u.role, u.active, s.created_at, s.expires_at
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ? AND u.active = 1
            """,
            (token,),
        ).fetchone()
        return dict(row) if row else None


def list_companies():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, name, code, active, created_at
            FROM companies
            WHERE active = 1
            ORDER BY name
            """
        ).fetchall()
        return [dict(row) for row in rows]


def list_migration_modules():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT module_key, title, status, notes, updated_at
            FROM migration_notes
            ORDER BY id
            """
        ).fetchall()
        return [dict(row) for row in rows]


def log_user_activity(user_id: int, action: str, detail: str = "", ip_address: str = ""):
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO user_activity_log (user_id, action, detail, ip_address, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, action, detail, ip_address, now),
        )


def get_user_activity(user_id: int, limit: int = 50):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.action, a.detail, a.ip_address, a.created_at
            FROM user_activity_log a
            WHERE a.user_id = ?
            ORDER BY a.created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def block_user(user_id: int, blocked_by: int, reason: str = ""):
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO user_blocklist (user_id, blocked_by, reason, created_at) VALUES (?, ?, ?, ?)",
            (user_id, blocked_by, reason, now),
        )
        conn.execute("UPDATE users SET active = 0 WHERE id = ?", (user_id,))
        # Revoca de inmediato todas las sesiones abiertas del usuario.
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def unblock_user(user_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM user_blocklist WHERE user_id = ?", (user_id,))
        conn.execute("UPDATE users SET active = 1 WHERE id = ?", (user_id,))


def is_user_blocked(user_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM user_blocklist WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row is not None


def list_blocked_users():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT b.user_id, b.blocked_by, b.reason, b.created_at,
                   u.username, u.full_name
            FROM user_blocklist b
            JOIN users u ON u.id = b.user_id
            ORDER BY b.created_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]
