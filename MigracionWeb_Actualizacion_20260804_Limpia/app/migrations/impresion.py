from datetime import datetime, timezone

from app.db import get_connection
from app.migrations import register
from app.migrations.base import Migration, _update_status

ORIGIN_TABLE = "formatos_impresion"


class ImpresionMigration(Migration):
    key = "impresion"
    title = "Impresion"
    description = "Migra configuracion de formatos de impresion desde MySQL"

    def _ensure_target(self):
        with get_connection() as sqlite:
            sqlite.execute("""
                CREATE TABLE IF NOT EXISTS impresion_migracion (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    empresa TEXT, tipo_formato TEXT, nombre TEXT,
                    contenido TEXT, activo INTEGER DEFAULT 1,
                    migrado_en TEXT
                )
            """)

    def preview(self) -> dict:
        conn, cur = self._get_mysql_cursor()
        try:
            try:
                cur.execute(f"SELECT COUNT(*) AS total FROM {ORIGIN_TABLE}")
                total = cur.fetchone()["total"]
            except Exception:
                return {"ok": True, "total": 0, "message": "Tabla no existe en MySQL."}
            return {"ok": True, "total": total}
        finally:
            cur.close()
            conn.close()

    def run(self) -> dict:
        conn, cur = self._get_mysql_cursor()
        with get_connection() as sqlite:
            try:
                self._ensure_target()
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")

                try:
                    cur.execute(f"SELECT * FROM {ORIGIN_TABLE}")
                    columns = [desc[0] for desc in cur.description]
                except Exception:
                    _update_status(self.key, "omitido", "Tabla no existe en MySQL.")
                    return {"ok": True, "migrados": 0, "message": "Tabla formatos_impresion no existe en origen."}

                migrados = 0
                for row in cur.fetchall():
                    row_dict = dict(zip(columns, row))
                    sqlite.execute(
                        """INSERT OR REPLACE INTO impresion_migracion
                           (empresa, tipo_formato, nombre, contenido, activo, migrado_en)
                           VALUES (?,?,?,?,?,?)""",
                        (
                            row_dict.get("empresa", ""),
                            row_dict.get("tipo_formato", ""),
                            row_dict.get("nombre", ""),
                            row_dict.get("contenido", ""),
                            1, now,
                        ),
                    )
                    migrados += 1

                _update_status(self.key, "listo", f"{migrados} formatos migrados.")
                return {"ok": True, "migrados": migrados, "message": f"{migrados} formatos migrados."}
            finally:
                cur.close()
                conn.close()


register(ImpresionMigration())
