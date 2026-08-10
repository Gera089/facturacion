from datetime import datetime, timezone

from app.db import get_connection
from app.migrations import register
from app.migrations.base import Migration, _update_status


class CobranzaMigration(Migration):
    key = "cobranza"
    title = "Cobranza"
    description = "Migra datos de cobranza (pagos, saldos) desde MySQL"

    def _ensure_target(self):
        with get_connection() as sqlite:
            sqlite.execute("""
                CREATE TABLE IF NOT EXISTS cobranza_migracion (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tipo TEXT, empresa TEXT, cliente_numero TEXT,
                    cliente_nombre TEXT, factura_id TEXT, folio TEXT,
                    monto REAL, fecha TEXT, referencia TEXT,
                    migrado_en TEXT
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
            abonos = self._try_query(cur, "SELECT COUNT(*) AS total FROM abonos")
            saldos = self._try_query(cur, "SELECT COUNT(*) AS total FROM clientes_saldos")
            return {
                "ok": True,
                "abonos": abonos[0]["total"] if abonos else 0,
                "saldos": saldos[0]["total"] if saldos else 0,
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

                abonos = self._try_query(cur, """
                    SELECT empresa, cliente_numero, cliente_nombre,
                           factura_id, monto, fecha, referencia
                    FROM abonos ORDER BY fecha
                """)
                if abonos:
                    for row in abonos:
                        sqlite.execute(
                            """INSERT OR REPLACE INTO cobranza_migracion
                               (tipo, empresa, cliente_numero, cliente_nombre,
                                factura_id, monto, fecha, referencia, migrado_en)
                               VALUES (?,?,?,?,?,?,?,?,?)""",
                            ("abono", row.get("empresa"), row.get("cliente_numero"),
                             row.get("cliente_nombre"), row.get("factura_id"),
                             row.get("monto"), row.get("fecha"), row.get("referencia"), now),
                        )
                        migrados += 1

                saldos = self._try_query(cur, """
                    SELECT empresa, cliente_numero, saldo, fecha
                    FROM clientes_saldos ORDER BY empresa, cliente_numero
                """)
                if saldos:
                    for row in saldos:
                        sqlite.execute(
                            """INSERT OR REPLACE INTO cobranza_migracion
                               (tipo, empresa, cliente_numero, monto, fecha, migrado_en)
                               VALUES (?,?,?,?,?,?)""",
                            ("saldo", row.get("empresa"), row.get("cliente_numero"),
                             row.get("saldo"), row.get("fecha"), now),
                        )
                        migrados += 1

                _update_status(self.key, "listo", f"{migrados} registros migrados.")
                return {"ok": True, "migrados": migrados}
            finally:
                cur.close()
                conn.close()


register(CobranzaMigration())
