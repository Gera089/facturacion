from datetime import datetime, timezone
from decimal import Decimal

from app.db import get_connection
from app.migrations import register
from app.migrations.base import Migration, _get_sqlite, _update_status

ORIGIN_TABLE = "clientes"
COLUMNS = [
    "numero", "nombre", "empresa", "razon_social", "calle", "no_exterior",
    "no_interior", "colonia", "alcaldia", "municipio", "codigo_postal",
    "poblacion", "estado", "pais", "rfc", "telefono", "correo_electronico",
    "contacto1", "contacto2", "dias_credito", "consignatario", "consig_calle",
    "consig_no_exterior", "consig_no_interior", "consig_colonia",
    "consig_delegacion", "consig_municipio", "consig_codigo_postal",
    "consig_poblacion", "consig_estado", "consig_pais", "zona", "no_proveedor",
    "agente", "descuento", "especial", "tipo", "vendedor", "direccion_entrega",
    "observaciones",
]


def _safe_val(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return v


class ClientesMigration(Migration):
    key = "clientes"
    title = "Clientes"
    description = "Migra catalogo de clientes desde MySQL"

    def _ensure_target(self, sqlite):
        cols = ", ".join(f"{c} TEXT" for c in COLUMNS)
        sqlite.execute(f"""
            CREATE TABLE IF NOT EXISTS cliente_migracion (
                {cols},
                migrado_en TEXT,
                PRIMARY KEY (numero, empresa)
            )
        """)
        sqlite.execute("""
            CREATE TABLE IF NOT EXISTS migracion_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                modulo TEXT NOT NULL,
                tipo TEXT NOT NULL,
                registros INTEGER,
                detalle TEXT,
                creado_en TEXT NOT NULL
            )
        """)
        sqlite.commit()

    def preview(self) -> dict:
        conn, cur = self._get_mysql_cursor()
        try:
            cur.execute(f"SELECT COUNT(*) AS total FROM {ORIGIN_TABLE}")
            total = cur.fetchone()["total"]
            cur.execute(f"SELECT COUNT(DISTINCT empresa) AS empresas FROM {ORIGIN_TABLE}")
            empresas = cur.fetchone()["empresas"]
            cur.execute(f"SELECT empresa, COUNT(*) AS cnt FROM {ORIGIN_TABLE} GROUP BY empresa ORDER BY cnt DESC LIMIT 20")
            por_empresa = [{"empresa": r["empresa"], "count": r["cnt"]} for r in cur.fetchall()]
            return {"ok": True, "total": total, "empresas": empresas, "por_empresa": por_empresa}
        finally:
            cur.close()
            conn.close()

    def run(self) -> dict:
        conn, cur = self._get_mysql_cursor()
        sqlite = _get_sqlite()
        try:
            self._ensure_target(sqlite)
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")

            cur.execute(f"SELECT {','.join(COLUMNS)} FROM {ORIGIN_TABLE} ORDER BY empresa, numero")
            rows = cur.fetchall()
            if not rows:
                sqlite.execute("INSERT INTO migracion_log (modulo, tipo, registros, detalle, creado_en) VALUES (?,?,?,?,?)",
                               (self.key, "completa", 0, "Sin registros", now))
                sqlite.commit()
                sqlite.close()
                _update_status(self.key, "listo", "Sin registros.")
                return {"ok": True, "migrados": 0, "message": "Sin clientes para migrar."}

            cols = ",".join(COLUMNS)
            placeholders = ",".join(["?"] * len(COLUMNS))
            migrados = 0

            for row in rows:
                values = [_safe_val(row[c]) for c in COLUMNS] + [now]
                sqlite.execute(
                    f"""
                    INSERT OR REPLACE INTO cliente_migracion ({cols}, migrado_en)
                    VALUES ({placeholders}, ?)
                    """,
                    values,
                )
                migrados += 1

            sqlite.execute("INSERT INTO migracion_log (modulo, tipo, registros, detalle, creado_en) VALUES (?,?,?,?,?)",
                           (self.key, "completa", migrados, f"Migrados {migrados} clientes", now))
            sqlite.commit()
            sqlite.close()
            _update_status(self.key, "listo", f"{migrados} clientes migrados.")
            return {"ok": True, "migrados": migrados, "message": f"{migrados} clientes migrados correctamente."}
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass


register(ClientesMigration())
