from datetime import datetime, timezone

from app.db import get_connection
from app.migrations import register
from app.migrations.base import Migration, _update_status


class ReportesMigration(Migration):
    key = "reportes"
    title = "Reportes"
    description = "Migra configuracion de reportes y datos agregados desde MySQL"

    def _ensure_target(self):
        with get_connection() as sqlite:
            sqlite.execute("""
                CREATE TABLE IF NOT EXISTS reporte_migracion (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tipo TEXT, nombre TEXT, configuracion TEXT,
                    creado_en TEXT, migrado_en TEXT
                )
            """)

    def _try_query(self, cur, sql: str) -> list[dict] | None:
        try:
            cur.execute(sql)
            cols = [desc[0] for desc in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception:
            return None

    def preview(self) -> dict:
        conn, cur = self._get_mysql_cursor()
        try:
            reportes = self._try_query(cur, "SELECT COUNT(*) AS total FROM reportes_config")
            return {
                "ok": True,
                "reportes": reportes[0]["total"] if reportes else 0,
            }
        finally:
            cur.close()
            conn.close()

    def run(self) -> dict:
        conn, cur = self._get_mysql_cursor()
        with get_connection() as sqlite:
            try:
                self._ensure_target()
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                migrados = 0

                rows = self._try_query(cur, "SELECT * FROM reportes_config ORDER BY nombre")
                if not rows:
                    _update_status(self.key, "omitido", "Sin reportes configurados en origen.")
                    return {"ok": True, "migrados": 0, "message": "Sin reportes para migrar."}

                for row in rows:
                    sqlite.execute(
                        """INSERT OR REPLACE INTO reporte_migracion
                           (tipo, nombre, configuracion, creado_en, migrado_en)
                           VALUES (?,?,?,?,?)""",
                        (row.get("tipo", ""), row.get("nombre", ""),
                         row.get("configuracion", ""), row.get("creado_en", now), now),
                    )
                    migrados += 1

                _update_status(self.key, "listo", f"{migrados} reportes migrados.")
                return {"ok": True, "migrados": migrados}
            finally:
                cur.close()
                conn.close()


register(ReportesMigration())
