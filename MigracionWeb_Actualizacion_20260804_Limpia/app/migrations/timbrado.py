from datetime import datetime, timezone

from app.db import get_connection
from app.migrations import register
from app.migrations.base import Migration, _update_status


class TimbradoMigration(Migration):
    key = "timbrado"
    title = "Timbrado"
    description = "Migra informacion de timbrado CFDI desde MySQL"

    def _ensure_target(self):
        with get_connection() as sqlite:
            sqlite.execute("""
                CREATE TABLE IF NOT EXISTS timbrado_migracion (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uuid TEXT, factura_id TEXT, empresa TEXT,
                    folio TEXT, serie TEXT, fecha_timbrado TEXT,
                    xml_base64 TEXT, migrado_en TEXT
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
            timbres = self._try_query(cur, "SELECT COUNT(*) AS total FROM timbrados")
            return {
                "ok": True,
                "timbrados": timbres[0]["total"] if timbres else 0,
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

                rows = self._try_query(cur, """
                    SELECT uuid, factura_id, empresa, folio, serie,
                           fecha_timbrado, xml
                    FROM timbrados ORDER BY fecha_timbrado
                """)
                if not rows:
                    _update_status(self.key, "omitido", "No hay timbrados en origen.")
                    return {"ok": True, "migrados": 0, "message": "Sin timbrados para migrar."}

                for row in rows:
                    sqlite.execute(
                        """INSERT OR REPLACE INTO timbrado_migracion
                           (uuid, factura_id, empresa, folio, serie,
                            fecha_timbrado, xml_base64, migrado_en)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (row.get("uuid"), str(row.get("factura_id", "")),
                         row.get("empresa"), row.get("folio"), row.get("serie"),
                         row.get("fecha_timbrado"), row.get("xml"), now),
                    )
                    migrados += 1

                _update_status(self.key, "listo", f"{migrados} timbrados migrados.")
                return {"ok": True, "migrados": migrados}
            finally:
                cur.close()
                conn.close()


register(TimbradoMigration())
