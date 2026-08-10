from datetime import datetime, timezone
from decimal import Decimal

from app.db import get_connection
from app.migrations import register
from app.migrations.base import Migration, _get_sqlite, _update_status

ORIGIN_TABLE = "facturas"
COLUMNS = [
    "id", "fecha", "numero_cliente", "cliente_nombre", "numero_salida",
    "comanda", "consignatario", "factura", "subtotal", "descuento_pct",
    "descuento", "iva", "total", "sae_codigo", "estatus",
    "timbrado_estatus", "timbrado_requerido", "cfdi_uuid", "empresa",
    "rfc", "vendedor", "lista_precios", "cargo_rebanado_pct",
]


def _safe_val(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return v


class FacturacionMigration(Migration):
    key = "facturacion"
    title = "Facturacion"
    description = "Migra facturas desde MySQL"

    def _ensure_target(self, sqlite):
        cols = ", ".join(f"{c} TEXT" for c in COLUMNS)
        sqlite.execute(f"""
            CREATE TABLE IF NOT EXISTS factura_migracion (
                {cols}, migrado_en TEXT,
                PRIMARY KEY (id, empresa)
            )
        """)
        sqlite.commit()

    def preview(self) -> dict:
        conn, cur = self._get_mysql_cursor()
        try:
            cur.execute(f"SELECT COUNT(*) AS total FROM {ORIGIN_TABLE}")
            total = cur.fetchone()["total"]
            cur.execute(f"SELECT estatus, COUNT(*) AS cnt FROM {ORIGIN_TABLE} GROUP BY estatus")
            por_estatus = [{"estatus": r["estatus"], "count": r["cnt"]} for r in cur.fetchall()]
            cur.execute(f"SELECT MIN(fecha) AS desde, MAX(fecha) AS hasta FROM {ORIGIN_TABLE}")
            rango = cur.fetchone()
            return {
                "ok": True, "total": total,
                "por_estatus": por_estatus,
                "desde": rango["desde"], "hasta": rango["hasta"],
            }
        finally:
            cur.close()
            conn.close()

    def run(self) -> dict:
        conn, cur = self._get_mysql_cursor()
        sqlite = _get_sqlite()
        try:
            self._ensure_target(sqlite)
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            cols = ",".join(COLUMNS)
            placeholders = ",".join(["?"] * (len(COLUMNS) + 1))

            cur.execute(f"SELECT {cols} FROM {ORIGIN_TABLE} ORDER BY fecha")
            rows = cur.fetchall()
            if not rows:
                sqlite.close()
                _update_status(self.key, "listo", "Sin facturas.")
                return {"ok": True, "migrados": 0, "message": "Sin facturas para migrar."}

            migrados = 0
            for row in rows:
                values = [_safe_val(row[c]) for c in COLUMNS] + [now]
                sqlite.execute(
                    f"INSERT OR REPLACE INTO factura_migracion ({cols}, migrado_en) VALUES ({placeholders})",
                    values,
                )
                migrados += 1

            sqlite.commit()
            sqlite.close()
            _update_status(self.key, "listo", f"{migrados} facturas migradas.")
            return {"ok": True, "migrados": migrados, "message": f"{migrados} facturas migradas."}
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass


register(FacturacionMigration())
