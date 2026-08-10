from datetime import datetime, timezone
from decimal import Decimal

from app.db import get_connection
from app.migrations import register
from app.migrations.base import Migration, _get_sqlite, _update_status

ORIGIN_TABLE = "productos"
COLUMNS = [
    "cip", "descripcion", "unidad", "tipo_lista", "iva", "descuento",
    "codigo_barras",
]


def _safe_val(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return v


class ProductosMigration(Migration):
    key = "productos"
    title = "Productos"
    description = "Migra catalogo de productos y listas de precios desde MySQL"

    def _ensure_target(self, sqlite):
        cols = ", ".join(f"{c} TEXT" for c in COLUMNS)
        sqlite.execute(f"""
            CREATE TABLE IF NOT EXISTS producto_migracion (
                {cols},
                migrado_en TEXT
            )
        """)
        sqlite.execute("""
            CREATE TABLE IF NOT EXISTS lista_precio_migracion (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo TEXT NOT NULL, cliente TEXT, descripcion TEXT,
                precio REAL, moneda TEXT, migrado_en TEXT
            )
        """)
        sqlite.commit()

    def preview(self) -> dict:
        conn, cur = self._get_mysql_cursor()
        try:
            cur.execute(f"SELECT COUNT(*) AS total FROM {ORIGIN_TABLE}")
            productos = cur.fetchone()["total"]
            cur.execute("SELECT COUNT(*) AS total FROM listas_precios")
            precios = cur.fetchone()["total"]
            return {"ok": True, "productos": productos, "listas_precios": precios}
        finally:
            cur.close()
            conn.close()

    def run(self) -> dict:
        conn, cur = self._get_mysql_cursor()
        sqlite = _get_sqlite()
        try:
            self._ensure_target(sqlite)
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            migrados = 0

            cur.execute(f"SELECT {','.join(COLUMNS)} FROM {ORIGIN_TABLE} ORDER BY cip")
            for row in cur.fetchall():
                values = [_safe_val(row[c]) for c in COLUMNS] + [now]
                placeholders = ",".join(["?"] * (len(COLUMNS) + 1))
                cols = ",".join(COLUMNS) + ",migrado_en"
                sqlite.execute(
                    f"INSERT OR REPLACE INTO producto_migracion ({cols}) VALUES ({placeholders})",
                    values,
                )
                migrados += 1

            # Migrate prices from precios table
            cur.execute("SELECT cip, cliente_numero, empresa, precio FROM precios ORDER BY cip, cliente_numero")
            precios = 0
            for row in cur.fetchall():
                sqlite.execute(
                    "INSERT OR REPLACE INTO lista_precio_migracion (cip, cliente, descripcion, precio, moneda, migrado_en) VALUES (?,?,?,?,?,?)",
                    (_safe_val(row["cip"]), row["cliente_numero"], row["empresa"],
                     _safe_val(row["precio"]), "MXN", now),
                )
                precios += 1

            sqlite.commit()
            sqlite.close()
            detalle = f"{migrados} productos, {precios} precios."
            _update_status(self.key, "listo", detalle)
            return {"ok": True, "productos": migrados, "listas_precios": precios, "message": detalle}
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass


register(ProductosMigration())
